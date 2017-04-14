import argparse
import os
import string
import uuid
import tensorflow as tf
import hypergan as hg
import hyperchamber as hc
import matplotlib.pyplot as plt
from hypergan.loaders import *
from hypergan.util.hc_tf import *
from hypergan.generators import *

import math

def text_plot(size, filename, data, x):
    plt.clf()
    plt.figure(figsize=(2,2))
    data = np.squeeze(data)
    plt.plot(x)
    plt.plot(data)
    plt.xlim([0, size])
    plt.ylim([-2, 2.])
    plt.ylabel("Amplitude")
    plt.xlabel("Time")
    plt.savefig(filename)

def get_vocabulary():
    lookup_keys = list("~()\"'&+#@/789zyxwvutsrqponmlkjihgfedcba ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456:-,;!?.")
    lookup_values = np.arange(len(lookup_keys), dtype=np.float32)

    lookup = {}

    for i, key in enumerate(lookup_keys):
        lookup[key]=lookup_values[i]

    return lookup_keys, lookup


def sample_char(v):
    v = v.encode('ascii', errors='ignore')
    print(v)
def parse_args():
    parser = argparse.ArgumentParser(description='Train a 2d test!', add_help=True)
    parser.add_argument('--batch_size', '-b', type=int, default=32, help='Examples to include in each batch.  If using batch norm, this needs to be preserved when in server mode')
    parser.add_argument('--device', '-d', type=str, default='/gpu:0', help='In the form "/gpu:0", "/cpu:0", etc.  Always use a GPU (or TPU) to train')
    parser.add_argument('--format', '-f', type=str, default='png', help='jpg or png')
    parser.add_argument('--config', '-c', type=str, default=None, help='config name')
    parser.add_argument('--distribution', '-t', type=str, default='circle', help='what distribution to test, options are circle, modes')
    parser.add_argument('--sample_every', type=int, default=50, help='Samples the model every n epochs.')
    parser.add_argument('--save_every', type=int, default=30000, help='Saves the model every n epochs.')
    return parser.parse_args()

def no_regularizer(amt):
    return None
 
def custom_discriminator_config():
    return { 
            'create': custom_discriminator 
    }

def custom_generator_config():
    return { 
            'create': custom_generator
    }

def custom_discriminator(gan, config, x, g, xs, gs, prefix='d_'):
    net = tf.concat(axis=0, values=[x,g])
    net = linear(net, 256, scope=prefix+'lin1')
    net = layer_norm_1(int(net.get_shape()[0]))(net)
    net = tf.nn.relu(net)
    net = linear(net, 256, scope=prefix+'lin2')
    return net

def custom_generator(config, gan, net):
    net = linear(net, 256, scope="g_lin_proj")
    net = batch_norm_1(gan.config.batch_size, name='g_bn_1')(net)
    net = tf.nn.relu(net)
    net = linear(net, 32, scope="g_lin_proj3")
    net = tf.tanh(net)
    return [net]


def d_pyramid_search_config():
    return hg.discriminators.pyramid_discriminator.config(
	    activation=[tf.nn.relu, lrelu, tf.nn.relu6, tf.nn.elu],
            depth_increase=[1.5,1.7,2,2.1],
            final_activation=[tf.nn.relu, tf.tanh, None],
            layer_regularizer=[batch_norm_1, layer_norm_1, None],
            layers=[2,1],
            fc_layer_size=[32,16,8,4,2],
            fc_layers=[0,1,2],
            first_conv_size=[4,8,2,1],
            noise=[False, 1e-2],
            progressive_enhancement=[False],
            strided=[True, False],
            create=d_pyramid_create
    )

def g_resize_conv_search_config():
    return resize_conv_generator.config(
            z_projection_depth=[8,16,32],
            activation=[tf.nn.relu,tf.tanh,lrelu,resize_conv_generator.generator_prelu],
            final_activation=[None,tf.nn.tanh,resize_conv_generator.minmax],
            depth_reduction=[2,1.5,2.1],
            layer_filter=None,
            layer_regularizer=[layer_norm_1,batch_norm_1],
            block=[resize_conv_generator.standard_block, resize_conv_generator.inception_block, resize_conv_generator.dense_block],
            resize_image_type=[1],
            create_method=g_resize_conv_create
    )

def g_resize_conv_create(config, gan, net):
    gan.config.x_dims = [32,1]
    gan.config.channels = 1
    gs = resize_conv_generator.create(config,gan,net)
    filter = [1,4,8,1]
    stride = [1,4,8,1]
    #gs[0] = tf.nn.avg_pool(gs[0], ksize=filter, strides=stride, padding='SAME')
    #gs[0] = linear(tf.reshape(gs[0], [gan.config.batch_size, -1]), 2, scope="g_2d_lin")
    gs[-1] = tf.reshape(gs[-1], [gan.config.batch_size, -1])
    print("GS0", gs[-1], gs)
    return gs

def d_pyramid_create(gan, config, x, g, xs, gs, prefix='d_'):
    return hg.discriminators.pyramid_discriminator.discriminator(gan, config, x, g, xs, gs, prefix)

def batch_accuracy(a, b):
    "Each point of a is measured against the closest point on b.  Distance differences are added together."
    tiled_a = a
    tiled_a = tf.reshape(tiled_a, [int(tiled_a.get_shape()[0]), 1, int(tiled_a.get_shape()[1])])

    tiled_a = tf.tile(tiled_a, [1, int(tiled_a.get_shape()[0]), 1])

    tiled_b = b
    tiled_b = tf.reshape(tiled_b, [1, int(tiled_b.get_shape()[0]), int(tiled_b.get_shape()[1])])
    tiled_b = tf.tile(tiled_b, [int(tiled_b.get_shape()[0]), 1, 1])

    difference = tf.abs(tiled_a-tiled_b)
    difference = tf.reduce_min(difference, axis=1)
    difference = tf.reduce_sum(difference, axis=1)
    return tf.reduce_sum(difference, axis=0) 


args = parse_args()

def train():
    selector = hg.config.selector(args)
    config_name=args.config or "2d-measure-accuracy-"+str(uuid.uuid4())

    config = selector.random_config()
    config_filename = os.path.expanduser('~/.hypergan/configs/'+config_name+'.json')

    trainers = []

    rms_opts = {
        'g_momentum': [0,0.1,0.01,1e-6,1e-5,1e-1,0.9,0.999, 0.5],
        'd_momentum': [0,0.1,0.01,1e-6,1e-5,1e-1,0.9,0.999, 0.5],
        'd_decay': [0.8, 0.9, 0.99,0.999,0.995,0.9999,1],
        'g_decay': [0.8, 0.9, 0.99,0.999,0.995,0.9999,1],
        'clipped_gradients': [False, 1e-2],
        'clipped_d_weights': [False, 1e-2],
        'd_learn_rate': [1e-3,1e-4,5e-4,1e-6,4e-4, 5e-5],
        'g_learn_rate': [1e-3,1e-4,5e-4,1e-6,4e-4, 5e-5]
    }

    stable_rms_opts = {
        "clipped_d_weights": 0.01,
        "clipped_gradients": False,
        "d_decay": 0.995, "d_momentum": 1e-05,
        "d_learn_rate": 0.001,
        "g_decay": 0.995,
        "g_momentum": 1e-06,
        "g_learn_rate": 0.0005,
    }

    trainers.append(hg.trainers.rmsprop_trainer.config(**rms_opts))

    adam_opts = {}

    adam_opts = {
        'd_learn_rate': [1e-3,1e-4,5e-4,1e-2,1e-6],
        'g_learn_rate': [1e-3,1e-4,5e-4,1e-2,1e-6],
        'd_beta1': [0.9, 0.99, 0.999, 0.1, 0.01, 0.2, 1e-8],
        'd_beta2': [0.9, 0.99, 0.999, 0.1, 0.01, 0.2, 1e-8],
        'g_beta1': [0.9, 0.99, 0.999, 0.1, 0.01, 0.2, 1e-8],
        'g_beta2': [0.9, 0.99, 0.999, 0.1, 0.01, 0.2, 1e-8],
        'd_epsilon': [1e-8, 1, 0.1, 0.5],
        'g_epsilon': [1e-8, 1, 0.1, 0.5],
        'd_clipped_weights': [False, 0.01],
        'clipped_gradients': [False, 0.01]
    }

    trainers.append(hg.trainers.adam_trainer.config(**adam_opts))
    
    sgd_opts = {
        'd_learn_rate': [1e-3,1e-4,5e-4,1e-2,1e-6],
        'g_learn_rate': [1e-3,1e-4,5e-4,1e-2,1e-6],
        'd_clipped_weights': [False, 0.01],
        'clipped_gradients': [False, 0.01]
    }

    #trainers.append(hg.trainers.sgd_trainer.config(**sgd_opts))


    encoders = []

    projections = []
    projections.append([hg.encoders.uniform_encoder.modal, hg.encoders.uniform_encoder.identity])
    projections.append([hg.encoders.uniform_encoder.modal, hg.encoders.uniform_encoder.sphere, hg.encoders.uniform_encoder.identity])
    projections.append([hg.encoders.uniform_encoder.binary, hg.encoders.uniform_encoder.sphere])
    projections.append([hg.encoders.uniform_encoder.sphere, hg.encoders.uniform_encoder.identity])
    projections.append([hg.encoders.uniform_encoder.modal, hg.encoders.uniform_encoder.sphere])
    projections.append([hg.encoders.uniform_encoder.sphere, hg.encoders.uniform_encoder.identity, hg.encoders.uniform_encoder.gaussian])
    encoder_opts = {
            'z': [16],
            'modes': [2,4,8,16],
            'projections': projections
            }

    stable_encoder_opts = {
      "max": 1,
      "min": -1,
      "modes": 8,
      "projections": [[
        "function:hypergan.encoders.uniform_encoder.modal",
        "function:hypergan.encoders.uniform_encoder.sphere",
        "function:hypergan.encoders.uniform_encoder.identity"
      ]],
      "z": 16
    }

    losses = []

    lamb_loss_opts = {
        'reverse':[True, False],
        'reduce': [tf.reduce_mean,hg.losses.wgan_loss.linear_projection,tf.reduce_sum,tf.reduce_logsumexp],
        'labels': [
            [-1, 1, 0],
            [0, 1, 1],
            [0, -1, -1],
            [1, -1, 0],
            [0, -1, 1],
            [0, 1, -1],
            [0, 0.5, -0.5],
            [0.5, -0.5, 0],
            [0.5, 0, -0.5]
        ],
        'alpha':[0,1e-3,1e-2,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,0.99,0.999],
        'beta':[0,1e-3,1e-2,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,0.99,0.999]

    }
    lsgan_loss_opts = {
        'reduce': [tf.reduce_mean,hg.losses.wgan_loss.linear_projection,tf.reduce_sum,tf.reduce_logsumexp],
        'labels': [
            [-1, 1, 0],
            [0, 1, 1],
            [0, -1, -1],
            [1, -1, 0],
            [0, -1, 1],
            [0, 1, -1],
            [0, 0.5, -0.5],
            [0.5, -0.5, 0],
            [0.5, 0, -0.5]
        ]
    }


    wgan_loss_opts = {
        'reduce': [tf.reduce_mean,hg.losses.wgan_loss.linear_projection,tf.reduce_sum,tf.reduce_logsumexp],
        'discriminator': None,
        'reverse': [True, False]
    }

    stable_loss_opts = {
      "alpha": 0.5,
      "beta": [0.5, 0.8],
      "discriminator": None,
      "label_smooth": 0.26111111111111107,
      "labels": [[
        0,
        -1,
        -1
      ]],
      "reduce": "function:tensorflow.python.ops.math_ops.reduce_mean",
      "reverse": True
    }
    losses.append([hg.losses.wgan_loss.config(**wgan_loss_opts)])
    #losses.append([hg.losses.lamb_gan_loss.config(**lamb_loss_opts)])
    #losses.append([hg.losses.lamb_gan_loss.config(**stable_loss_opts)])
    #losses.append([hg.losses.lamb_gan_loss.config(**stable_loss_opts)])
    losses.append([hg.losses.lsgan_loss.config(**lsgan_loss_opts)])
    #losses.append([hg.losses.wgan_loss.config(**wgan_loss_opts)])


    #encoders.append([hg.encoders.uniform_encoder.config(**encoder_opts)])
    encoders.append([hg.encoders.uniform_encoder.config(**stable_encoder_opts)])
    custom_config = {
        'model': args.config,
        'batch_size': args.batch_size,
        'trainer': trainers,
        'generator': custom_generator_config(),
        'discriminators': [[custom_discriminator_config()]],
        'losses': losses,
        'encoders': encoders
    }

    custom_config_selector = hc.Selector()
    for key,value in custom_config.items():
        custom_config_selector.set(key, value)
        print("Set ", key, value)
    
    custom_config_selection = custom_config_selector.random_config()

    for key,value in custom_config_selection.items():
        config[key]=value

    
    config = selector.load_or_create_config(config_filename, config)
    config['dtype']=tf.float32
    config = hg.config.lookup_functions(config)
    print(config)

    def circle(x):
        spherenet = tf.square(x)
        spherenet = tf.reduce_sum(spherenet, 1)
        lam = tf.sqrt(spherenet)
        return x/tf.reshape(lam,[int(lam.get_shape()[0]), 1])

    def modes(x):
        return tf.round(x*2)/2.0

    if args.distribution == 'text':
        x = tf.constant("replicate this line 2")
        reader = tf.TextLineReader()
        filename_queue = tf.train.string_input_producer(["chargan.txt"])
        key, line = reader.read(filename_queue)
        x = line
        lookup_keys, lookup = get_vocabulary()

        table = tf.contrib.lookup.string_to_index_table_from_tensor(
            mapping = lookup_keys, default_value = 0)
        
        x = tf.string_join([x, tf.constant(" " * 32)]) 
        x = tf.substr(x, [0], [32])
        x = tf.string_split(x,delimiter='')
        x = tf.sparse_tensor_to_dense(x, default_value=' ')
        x = tf.reshape(x, [32])
        print("X___",x.get_shape())
        x = table.lookup(x)
        x = tf.cast(x, dtype=tf.float32)

        x -= len(lookup_keys)/2.0
        x /= len(lookup_keys)/2.0
        x = tf.reshape(x, [1, int(x.get_shape()[0])])
        x = tf.tile(x, [512, 1])
        num_preprocess_threads = 8
        x = tf.train.shuffle_batch(
          [x],
          batch_size=config.batch_size,
          num_threads=num_preprocess_threads,
          capacity= 512000,
          min_after_dequeue=51200,
          enqueue_many=True)

        #x=tf.decode_raw(x,tf.uint8)
        #x=tf.cast(x,tf.int32)
        #x = table.lookup(x)
        #x = tf.reshape(x, [64])
        #print("X IS ", x)
        #x = "replicate this line"


        #x=tf.cast(x, tf.float32)
        #x=x / 255.0 * 2 - 1

        #x = tf.constant("replicate this line")


        #--- working manual input ---
        #lookup_keys, lookup = get_vocabulary()

        #input_default = 'reproduce this line                                             '
        #input_default = [lookup[obj] for obj in list(input_default)]
        #
        #input_default = tf.constant(input_default)
        #input_default -= len(lookup_keys)/2.0
        #input_default /= len(lookup_keys)/2.0
        #input_default = tf.reshape(input_default, [1, 64])
        #input_default = tf.tile(input_default, [512, 1])

        #x = tf.placeholder_with_default(
        #        input_default, 
        #        [512, 64])

        #---/ working manual input ---

    initial_graph = {
            'x':x,
            'num_labels':1
            }

    print("Starting training for: "+config_filename)
    selector.save(config_filename, config)

    with tf.device(args.device):
        gan = hg.GAN(config, initial_graph)
        with gan.sess.as_default():
            table.init.run()
        tf.train.start_queue_runners(sess=gan.sess)

        accuracy_x_to_g=batch_accuracy(gan.graph.x, gan.graph.g[0])
        accuracy_g_to_x=batch_accuracy(gan.graph.g[0], gan.graph.x)
        s = [int(g) for g in gan.graph.g[0].get_shape()]
        gan.graph.g[0] = tf.reshape(gan.graph.g[0], [int(gan.graph.g[0].get_shape()[0]), -1])
        slice1 = tf.slice(gan.graph.g[0], [0,0], [s[0]//2, -1])
        slice2 = tf.slice(gan.graph.g[0], [s[0]//2,0], [s[0]//2, -1])
        accuracy_g_to_g=batch_accuracy(slice1, slice2)
        x_0 = gan.sess.run(gan.graph.x)
        z_0 = gan.sess.run(gan.graph.z[0])

        if args.config is not None:
            save_file = os.path.expanduser("~/.hypergan/saves/"+args.config+".ckpt")
            with tf.device('/cpu:0'):
                gan.load_or_initialize_graph(save_file)
        else:
            save_file = None
            gan.initialize_graph()

        ax_sum = 0
        ag_sum = 0
        diversity = 0.00001
        dlog = 0
        last_i = 0
        samples = 0

        tf.train.start_queue_runners(sess=gan.sess)

        limit = 10000
        if args.config:
            limit = 10000000
        for i in range(limit):
            d_loss, g_loss = gan.train()

            if(np.abs(d_loss) > 100 or np.abs(g_loss) > 100) and args.config is None:
                print("D_LOSS G_LOSS BREAK")
                ax_sum = ag_sum = 100000.00
                break

            if i % 1000 == 0 and i != 0: 
                ax, ag, agg, dl = gan.sess.run([accuracy_x_to_g, accuracy_g_to_x, accuracy_g_to_g, gan.graph.d_log], {gan.graph.x: x_0, gan.graph.z[0]: z_0})
                if (np.abs(ax) > 5000.0 or np.abs(ag) > 5000.0) and args.config is None:
                    print("ABS AX AG BREAK", np.abs(ax), np.abs(ag), args.config)
                    ax_sum = ag_sum = 100000.00
                    break


            #if(i % 10000 == 0 and i != 0):
            #    g_vars = [var for var in tf.trainable_variables() if 'g_' in var.name]
            #    init = tf.initialize_variables(g_vars)
            #    gan.sess.run(init)

            if(i > 9000 and args.config is None):
                ax, ag, agg, dl = gan.sess.run([accuracy_x_to_g, accuracy_g_to_x, accuracy_g_to_g, gan.graph.d_log], {gan.graph.x: x_0, gan.graph.z[0]: z_0})
                diversity += agg
                ax_sum += ax
                ag_sum += ag
                dlog = dl

            if i % args.sample_every == 0 and i > 0:
                g, x_val = gan.sess.run([gan.graph.gs[0], gan.graph.x], {gan.graph.z[0]:z_0})
                sample_file="samples/%06d.png" % (samples)
                text_plot(64, sample_file, g[0], x_0[0])
                samples+=1
                lookup_keys, lookup = get_vocabulary()
                lookup =  {i[1]:i[0] for i in lookup.items()} # reverse hash
                g *= len(lookup_keys)/2.0
                g += len(lookup_keys)/2.0
                x_val *= len(lookup_keys)/2.0
                x_val += len(lookup_keys)/2.0
                g = np.round(g)
                x_val = np.round(x_val)
                g = np.maximum(0, g)
                g = np.minimum(len(lookup_keys)-1, g)
                ox_val = [lookup[obj] for obj in list(x_val[0])]
                print("X:"+str(i))
                print("".join(ox_val))
                print("G:")
                for j, g0 in enumerate(g):
                    if j > 16:
                        break

                    og = [lookup[obj] for obj in list(g0)]
                    print("".join(og))

            if i % args.save_every == 0 and i > 0 and args.config is not None:
                print("Saving " + save_file)
                with tf.device('/cpu:0'):
                    gan.save(save_file)


        if args.config is None:
            with open("sequence-results-10k.csv", "a") as myfile:
                myfile.write(config_name+","+str(ax_sum)+","+str(ag_sum)+","+ str(ax_sum+ag_sum)+","+str(ax_sum*ag_sum)+","+str(dlog)+","+str(diversity)+","+str(ax_sum*ag_sum*(1/diversity))+","+str(last_i)+"\n")
        tf.reset_default_graph()
        gan.sess.close()

while(True):
    train()
