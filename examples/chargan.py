from common import *
from hypergan.viewer import GlobalViewer
from hyperchamber import Config
from hypergan.discriminators import *
from hypergan.distributions import *
from hypergan.gan_component import ValidationException, GANComponent
from hypergan.gans.base_gan import BaseGAN
from hypergan.generators import *
from hypergan.inputs import *
from hypergan.layer_shape import LayerShape
from hypergan.samplers import *
from hypergan.trainers import *
import argparse
import copy
import hyperchamber as hc
import hypergan as hg
import importlib
import json
import numpy as np
import os
import re
import string
import sys
import time
import torch
import torch.utils.data as data
import uuid

class TextData(data.Dataset):
    def __init__(self, path, length, device, mode):
        self.size = os.path.getsize(path)
        self.file = None
        self.path = path
        self.device = device
        self.length = length
        self.mode = mode

    def encode_line(self, line):
        return torch.tensor([(c/256.0 * 2 - 1) for c in line])

    def __getitem__(self, index):
        if self.file is None:
            self.file = open(self.path, 'rb')
        self.file.seek(index)
        data = self.file.read(self.length)
        if self.mode == "constant":
            encoded = torch.ones_like(self.encode_line(data).view(1, -1)) * 0.5
        else:
            encoded = self.encode_line(data).view(1, -1)
        return [encoded]

    def __len__(self):
        return self.size - self.length

    def pad_or_truncate(self, line):
        while(len(line) < self.length):
            line += [self.get_encoded_value(" ")]
        return line

    def sample_output(self, val):
        val = (np.reshape(val, [-1]) + 1.0) * 127.5
        x = val[0]
        val = np.round(val)

        ox_val = [chr(int(obj)) for obj in list(val)]
        string = "".join(ox_val)
        return string


class TextInput:
    def __init__(self, config, batch_size, filename, length, mode='seek', one_hot=False, device=0):
        self.textdata = TextData(filename, length, device=device, mode=mode)
        self.dataloader = data.DataLoader(self.textdata, batch_size=batch_size, shuffle=True, num_workers=1, drop_last=True)
        self.dataset = None
        self._batch_size = batch_size
        self.length = length
        self.filename = filename
        self.one_hot = one_hot
        self.device = device
        self.config = config

    def text_plot(self, size, filename, data, x):
        bs = x.shape[0]
        data = np.reshape(data, [bs, -1])
        x = np.reshape(x, [bs, -1])
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

    def to(self, device):
        return TextInput(self.config, self._batch_size, self.filename, self.length, self.one_hot, device=device)

    def next(self, index=0):
        if self.dataset is None:
            self.dataset = iter(self.dataloader)
        try:
            self.sample = self.dataset.next()[0].to(self.device)
            return self.sample
        except StopIteration:
            self.dataset = iter(self.dataloader)
            return self.next(index)

    def batch_size(self):
        return self._batch_size

    def channels(self):
        return 1#self.length

    def width(self):
        return 1

    def height(self):
        return self.length

class CharGAN(BaseGAN):
    def __init__(self, *args, **kwargs):
        BaseGAN.__init__(self, *args, **kwargs)
        self.x = self.inputs.next()

    def build(self):
        torch.onnx.export(self.generator, torch.randn(*self.latent.z.shape, device='cuda'), "generator.onnx", verbose=True, input_names=["latent"], output_names=["generator"])

    def required(self):
        return "generator".split()

    def create(self):
        self.latent = self.create_component("latent")
        self.generator = self.create_component("generator", input=self.latent, context_shapes={"q": LayerShape(self.config.length//2)})
        self.discriminator = self.create_component("discriminator")

    def forward_discriminator(self, inputs):
        return self.discriminator(inputs[0])

    def forward_pass(self):
        self.x = self.inputs.next()
        self.q, self.a = torch.split(self.x, self.config.length//2, dim=-1)
        self.augmented_latent = self.train_hooks.augment_latent(self.latent.next())
        a_g = self.generator(self.augmented_latent, context={"q": self.q}).view(self.q.shape)
        self.g = torch.cat([self.q, a_g], dim=-1)
        self.augmented_x = self.train_hooks.augment_x(self.x)
        self.augmented_g = self.train_hooks.augment_g(self.g)
        d_real = self.forward_discriminator([self.augmented_x])
        d_fake = self.forward_discriminator([self.augmented_g])
        self.d_fake = d_fake
        self.d_real = d_real
        return d_real, d_fake

    def input_nodes(self):
        "used in hypergan build"
        return [
        ]

    def output_nodes(self):
        "used in hypergan build"
        return [
        ]

    def discriminator_components(self):
        return [self.discriminator]

    def generator_components(self):
        return [self.generator]

    def discriminator_fake_inputs(self):
        return [[self.augmented_g]]

    def discriminator_real_inputs(self):
        if hasattr(self, 'augmented_x'):
            return [self.augmented_x]
        else:
            return [self.inputs.next()]

if __name__ == '__main__':
    arg_parser = ArgumentParser("Learn from a text file", require_directory=False)
    arg_parser.parser.add_argument('--one_hot', action='store_true', help='Use character one-hot encodings.')
    arg_parser.parser.add_argument('--filename', type=str, default='chargan.txt', help='Input dataset');
    arg_parser.parser.add_argument('--length', type=int, default=256, help='Length of string per line');
    args = arg_parser.parse_args()

    def lookup_config(args):
        if args.action != 'search':
            return hg.configuration.Configuration.load(args.config+".json")
        else:
            return hc.Config({"length": 1024})

    config = lookup_config(args)

    config_name = args.config
    save_file = "saves/"+config_name+"/model.ckpt"

    mode = "seek"

    if args.action == 'search':
        mode = 'constant'

    inputs = TextInput(config, args.batch_size, args.filename, config.length, mode=mode, one_hot=config.length)

    def parse_size(size):
        width = int(size.split("x")[0])
        height = int(size.split("x")[1])
        channels = int(size.split("x")[2])
        return [width, height, channels]

    def random_config_from_list(config_list_file):
        """ Chooses a random configuration from a list of configs (separated by newline) """
        lines = tuple(open(config_list_file, 'r'))
        config_file = random.choice(lines).strip()
        print("[hypergan] config file chosen from list ", config_list_file, '  file:', config_file)
        return hg.configuration.Configuration.load(config_file+".json")

    def setup_gan(config, inputs, args):
        if "encode" in config:
            print("CHARGAN")
            gan = CharGAN(config, inputs=inputs)
        else:
            gan = hg.GAN(config, inputs=inputs)
        gan.load(save_file)

        return gan

    def sample(config, inputs, args):
        gan = setup_gan(config, inputs, args)

    def search(config, inputs, args):
        metrics = train(config, inputs, args)
        return metrics

    def train(config, inputs, args):
        gan = setup_gan(config, inputs, args)
        trainable_gan = hg.TrainableGAN(gan, save_file = save_file, devices = args.devices, backend_name = args.backend)

        trainers = []

        x_0 = gan.inputs.next()
        z_0 = gan.latent.sample()

        ax_sum = 0
        ag_sum = 0
        diversity = 0.00001
        dlog = 0
        last_i = 0
        steps = 0
        metric = 100

        while(True):
            steps +=1
            if steps > args.steps and args.steps != -1:
                break
            trainable_gan.step()

            if args.action == 'train' and steps % args.save_every == 0 and steps > 0:
                print("saving " + save_file)
                trainable_gan.save()

            if steps % args.sample_every == 0 or steps == args.steps -1:
                x_val = gan.inputs.next()
                q, a = torch.split(x_val, x_val[0].shape[-1]//2, dim=-1)
                g = gan.generator.forward(gan.latent.sample(), context={"q": q})
                print("Query:")
                print(inputs.textdata.sample_output(q[0].cpu().detach().numpy()))
                print("X answer:")
                print(inputs.textdata.sample_output(a[0].cpu().detach().numpy()))
                print("G answer:")
                g_output = inputs.textdata.sample_output(g[0].cpu().detach().numpy())
                g_output = re.sub(r'[\x00-\x09\x7f-\x9f]', '�', g_output)
                print(g_output)
                print("Q mean:")
                print(q.mean())
                print("A mean:")
                print(a.mean())
                print("G mean:")
                print(g.mean())
                metric = (a-g).mean().cpu().detach().numpy()
                print("METRIC")
                print(metric)
        return [metric]

        #if args.config is None:
        #    with open("sequence-results-10k.csv", "a") as myfile:
        #        myfile.write(config_name+","+str(ax_sum)+","+str(ag_sum)+","+ str(ax_sum+ag_sum)+","+str(ax_sum*ag_sum)+","+str(dlog)+","+str(diversity)+","+str(ax_sum*ag_sum*(1/diversity))+","+str(last_i)+"\n")

    if args.action == 'train':
        metrics = train(config, inputs, args)
        print("Resulting metrics:", metrics)
    elif args.action == 'sample':
        sample(config, inputs, args)
    elif args.action == 'search':
        config_filename = "chargan-search-"+str(uuid.uuid4())+'.json'
        config = hc.Config(json.loads(open(os.getcwd()+'/chargan-search.json', 'r').read()))
        config.trainer["hooks"].append(
          {
            "class": "function:hypergan.train_hooks.inverse_train_hook.InverseTrainHook",
            "gamma": [random.choice([1, 10, 1e-1, 100]), random.choice([1, 10, 1e-1, 100])],
            "g_gamma": random.choice([1, 10, 1e-1, 100]),
            "only_real": random.choice([False, False, True]),
            "only_fake": random.choice([False, False, True]),
            "invert": random.choice([False, False, True])
          })
        config.trainer["optimizer"] = random.choice([{
            "class": "class:hypergan.optimizers.adamirror.Adamirror",
            "lr": random.choice(list(np.linspace(0.0001, 0.002, num=1000))),
            "betas":[random.choice([0.1, 0.9, 0.9074537537537538, 0.99, 0.999]),random.choice([0,0.9,0.997])]
        },{
            "class": "class:torch.optim.SGD",
            "lr": random.choice([1e-1, 1, 1e-2, 4e-2])
        },{
            "class": "class:torch.optim.RMSprop",
            "lr": random.choice([1e-3, 1e-4, 5e-4, 3e-3]),
            "alpha": random.choice([0.9, 0.99, 0.999]),
            "eps": random.choice([1e-8, 1e-13]),
            "weight_decay": random.choice([0, 1e-2]),
            "momentum": random.choice([0, 0.1, 0.9]),
            "centered": random.choice([False, True])
        },
        {

            "class": "class:torch.optim.Adam",
            "lr": 1e-3,
            "betas":[random.choice([0.1, 0.9, 0.9074537537537538, 0.99, 0.999]),random.choice([0,0.9,0.997])],
            "eps": random.choice([1e-8, 1e-13]),
            "weight_decay": random.choice([0, 1e-2]),
            "amsgrad": random.choice([False, True])
            }

        ])

     


        metric_sum = search(config, inputs, args)
        search_output = "chargan-search-results.csv"
        hc.Selector().save(config_filename, config)
        with open(search_output, "a") as myfile:
            total = sum(metric_sum)
            myfile.write(config_filename+","+",".join([str(x) for x in metric_sum])+","+str(total)+"\n")

    else:
        print("Unknown action: "+args.action)

GlobalViewer.close()
