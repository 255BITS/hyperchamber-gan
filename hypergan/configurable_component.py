import hyperchamber as hc
import inspect
import copy
import re
import os
import operator
from functools import reduce

import hypergan
import torch
import torch.nn as nn

from .gan_component import GANComponent
from hypergan.gan_component import ValidationException

from hypergan.modules.adaptive_instance_norm import AdaptiveInstanceNorm
from hypergan.modules.concat_noise import ConcatNoise
from hypergan.modules.learned_noise import LearnedNoise
from hypergan.modules.modulated_conv2d import ModulatedConv2d, Blur, EqualLinear
from hypergan.modules.reshape import Reshape
from hypergan.modules.no_op import NoOp
from hypergan.modules.residual import Residual
from hypergan.modules.variational import Variational
from hypergan.modules.pixel_norm import PixelNorm

class ConfigurationException(Exception):
    pass

class ConfigurableComponent(GANComponent):
    def __init__(self, gan, config, input=None, context={}):
        self.current_channels = gan.channels()
        self.current_width = gan.width()
        self.current_height = gan.height()
        self.current_input_size = self.current_channels * self.current_width * self.current_height
        if input:
            self.current_channels = input.current_channels
            self.current_width = input.current_width
            self.current_height = input.current_height
            self.current_input_size = input.current_input_size
        self.layers = []
        self.layer_sizes = {}
        self.nn_layers = []
        self.layer_options = {}
        self.parsed_layers = []
        self.parser = hypergan.parser.Parser()
        self.layer_ops = {**self.activations(),
            "adaptive_avg_pool": self.layer_adaptive_avg_pool,
            "adaptive_avg_pool1d": self.layer_adaptive_avg_pool1d,
            "adaptive_avg_pool3d": self.layer_adaptive_avg_pool3d,
            "adaptive_instance_norm": self.layer_adaptive_instance_norm,
            "add": self.layer_add,
            "avg_pool": self.layer_avg_pool,
            "batch_norm": self.layer_batch_norm,
            "batch_norm1d": self.layer_batch_norm1d,
            "blur": self.layer_blur,
            "concat": self.layer_concat,
            #"concat3d": self.layer_concat3d,
            "conv": self.layer_conv,
            "conv1d": self.layer_conv1d,
            "conv3d": self.layer_conv3d,
            "deconv": self.layer_deconv,
            "dropout": self.layer_dropout,
            "equal_linear": self.layer_equal_linear,
            "flatten": nn.Flatten(),
            "identity": self.layer_identity,
            "initializer": self.layer_initializer,
            "instance_norm": self.layer_instance_norm,
            "instance_norm1d": self.layer_instance_norm1d,
            "instance_norm3d": self.layer_instance_norm3d,
            "latent": self.layer_latent,
            "layer": self.layer_layer,
            "layer_norm": self.layer_norm,
            "linear": self.layer_linear,
            #"make2d": self.layer_make2d,
            #"make3d": self.layer_make3d,
            "modulated_conv2d": self.layer_modulated_conv2d,
            "pad": self.layer_pad,
            "pixel_norm": self.layer_pixel_norm,
            "reshape": self.layer_reshape,
            "residual": self.layer_residual, #TODO options
            "resize_conv": self.layer_resize_conv,
            "resize_conv1d": self.layer_resize_conv1d,
            "split": self.layer_split,
            "subpixel": self.layer_subpixel,
            "upsample": self.layer_upsample,
            "vae": self.layer_vae
            #"add": self.layer_add, #TODO
            # "crop": self.layer_crop,
            # "dropout": self.layer_dropout,
            # "noise": self.layer_noise, #TODO
            #"attention": self.layer_attention, #TODO
            #"const": self.layer_const, #TODO
            #"gram_matrix": self.layer_gram_matrix, #TODO
            #"image_statistics": self.layer_image_statistics, #TODO
            #"knowledge_base": self.layer_knowledge_base, #TODO
            #"layer_filter": self.layer_filter, #TODO
            #"layer_norm": self.layer_layer_norm,#TODO
            #"mask": self.layer_mask,#TODO
            #"match_support": self.layer_match_support,#TODO
            #"minibatch": self.layer_minibatch,#TODO
            #"pixel_norm": self.layer_pixel_norm,#TODO
            #"progressive_replace": self.layer_progressive_replace,#TODO
            #"reduce_sum": self.layer_reduce_sum,#TODO might want to just do "reduce sum" instead
            #"relational": self.layer_relational,#TODO
            #"unpool": self.layer_unpool, #TODO https://arxiv.org/abs/1505.04366
            #"squash": self.layer_squash, #TODO
            #"tensorflowcv": self.layer_tensorflowcv, #TODO layer torchvision instead?
            #"turing_test": self.layer_turing_test, #TODO
            #"two_sample_stack": self.layer_two_sample_stack, #TODO
            #"zeros": self.layer_zeros, #TODO
            #"zeros_like": self.layer_zeros_like #TODO
            }
        self.named_layers = {}
        self.context = context
        if not hasattr(gan, "named_layers"):
            gan.named_layers = {}
        self.subnets = hc.Config(hc.Config(config).subnets or {})
        GANComponent.__init__(self, gan, config)

    def required(self):
        return "layers".split()

    def layer(self, name):
        if name in self.gan.named_layers:
            return self.gan.named_layers[name] 
        if name in self.named_layers:
            return self.named_layers[name]
        return None

    def build(self, net, replace_controls={}, context={}):
        self.replace_controls=replace_controls
        config = self.config

        for name, layer in self.context.items():
            self.set_layer(name, layer)

        for name, layer in context.items():
            self.set_layer(name, layer)

        for layer in config.layers:
            net = self.parse_layer(net, layer)
            self.layers += [net]

        return net

    def create(self):
        for layer in self.config.layers:
            net = self.parse_layer(layer)
            self.nn_layers.append(net)

        self.net = nn.Sequential(*self.nn_layers)

    def parse_layer(self, layer):
        config = self.config

        if isinstance(layer, list):
            ns = []
            axis = -1
            for l in layer:
                if isinstance(l, int):
                    axis = l
                    continue
                n = self.parse_layer(l)
                ns += [n]

        else:
            print("Parsing layer:", layer)
            parsed = self.parser.parse_string(layer)
            parsed.parsed_options = hc.Config(parsed.options)
            print("Parsed layer:", parsed.to_list())
            self.parsed_layers.append(parsed)
            return self.build_layer(parsed.layer_name, parsed.args, parsed.parsed_options)

    def build_layer(self, op, args, options):
        if self.layer_ops[op]:
            if isinstance(self.layer_ops[op], nn.Module):
                net = self.layer_ops[op]
            else:
                #before_count = self.count_number_trainable_params()
                net = self.layer_ops[op](None, args, options)
            if 'name' in options:
                self.set_layer(options['name'], net)
            return net

            #after = self.variables()
            #new = set(after) - set(before)
            #for j in new:
            #    self.layer_options[j]=options
            #after_count = self.count_number_trainable_params()
            #if not self.ops._reuse:
            #    if net == None:
            #        print("[Error] Layer resulted in null return value: ", op, args, options)
            #        raise ValidationException("Configurable layer is null")
            #    print("layer: ", self.ops.shape(net), op, args, after_count-before_count, "params")
        else:
            print("ConfigurableComponent: Op not defined", op)

    def set_layer(self, name, net):
        self.gan.named_layers[name] = net
        self.named_layers[name]     = net
        self.layer_sizes[name] = [self.current_width, self.current_height, self.current_channels]
        if name == "w":
            self.adaptive_instance_norm_size = self.current_input_size

    def activations(self):
        return {
            "celu": nn.CELU(),
            "gelu": nn.GELU(),
            "lrelu": nn.LeakyReLU(0.2),
            "prelu": nn.PReLU(),
            "relu": nn.ReLU(),
            "relu6": nn.ReLU6(),
            "selu": nn.SELU(),
            "sigmoid": nn.Sigmoid(),
            "softplus": nn.Softplus(),
            "softshrink": nn.Softshrink(),
            "softsign": nn.Softsign(),
            "hardtanh": nn.Hardtanh(),
            "tanh": nn.Tanh(),
            "tanhshrink": nn.Tanhshrink()
        }

    def layer_residual(self, net, args, options):
        return Residual(self.current_channels)

    def layer_dropout(self, net, args, options):
        return nn.Dropout2d(float(args[0]))

    def layer_identity(self, net, args, options):
        return NoOp()

    def layer_equal_linear(self, net, args, options):
        result = EqualLinear(self.current_input_size,args[0])
        self.current_input_size = args[0]
        return result

    def get_same_padding(self, input_rows, filter_rows, stride, dilation):
        out_rows = (input_rows + stride - 1) // stride
        return max(0, (out_rows - 1) * stride + (filter_rows - 1) * dilation + 1 - input_rows) // 2

    def layer_conv(self, net, args, options):
        if len(args) > 0:
            channels = args[0]
        else:
            channels = self.current_channels
        print("Options:", options)
        options = hc.Config(options)
        stride = options.stride or 1
        fltr = options.filter or 3
        dilation = 1

        padding = options.padding or 1#self.get_same_padding(self.current_width, self.current_width, stride, dilation)

        print("conv start", self.current_width, self.current_height, self.current_channels, stride)
        layers = [nn.Conv2d(options.input_channels or self.current_channels, channels, fltr, stride, padding = (padding, padding))]
        self.last_logit_layer = layers[0]
        self.current_channels = channels
        if stride > 1:
            self.current_width = self.current_width // stride #TODO
            self.current_height = self.current_height // stride #TODO
        print("conv", self.current_width, self.current_height, self.current_channels, stride)
        self.current_input_size = self.current_channels * self.current_width * self.current_height
        return nn.Sequential(*layers)

    def layer_conv1d(self, net, args, options):
        if len(args) > 0:
            channels = args[0]
        else:
            channels = self.current_channels
        print("Options:", options)
        options = hc.Config(options)
        stride = options.stride or 1
        fltr = options.filter or 3
        dilation = 1

        padding = options.padding or 1#self.get_same_padding(self.current_width, self.current_width, stride, dilation)

        print("conv start", self.current_width, self.current_height, self.current_channels, stride)
        layers = [nn.Conv1d(options.input_channels or self.current_channels, channels, fltr, stride, padding = padding)]
        self.last_logit_layer = layers[0]
        self.current_channels = channels
        if stride > 1:
            self.current_height = self.current_height // stride #TODO
        print("conv", self.current_width, self.current_height, self.current_channels, stride)
        self.current_input_size = self.current_channels * self.current_width * self.current_height
        return nn.Sequential(*layers)


    def layer_conv3d(self, net, args, options):
        if len(args) > 0:
            channels = args[0]
        else:
            channels = self.current_channels
        options = hc.Config(options)
        stride = options.stride or 1
        fltr = options.filter or 3
        dilation = 1

        padding = options.padding or 1#self.get_same_padding(self.current_width, self.current_width, stride, dilation)
        print("PADDING", padding)

        layers = [nn.Conv3d(options.input_channels or self.current_channels, channels, fltr, stride, padding = padding)]
        self.last_logit_layer = layers[0]
        self.current_channels = channels
        if stride > 1:
            self.current_width = self.current_width // stride #TODO
            self.current_height = self.current_height // stride #TODO
            print("conv", self.current_width, self.current_height, self.current_channels)
        self.current_input_size = self.current_channels * self.current_width * self.current_height
        return nn.Sequential(*layers)

    def layer_linear(self, net, args, options):
        options = hc.Config(options)
        shape = [int(x) for x in str(args[0]).split("*")]
        bias = True
        if options.bias == False:
            bias = False
        output_size = 1
        for dim in shape:
            output_size *= dim
        print("+", options.input_size or self.current_input_size, self.current_width, self.current_height, self.current_channels)
        layers = [
            nn.Linear(options.input_size or self.current_input_size, output_size, bias=bias)
        ]
        self.last_logit_layer = layers[0]
        if len(shape) == 3:
            self.current_channels = shape[2]
            self.current_width = shape[0]
            self.current_height = shape[1]
            layers.append(Reshape(self.current_channels, self.current_height, self.current_width))

        if len(shape) == 2:
            self.current_channels = shape[1]
            self.current_width = 1
            self.current_height = shape[0]
            layers.append(Reshape(self.current_channels, self.current_height))


        self.current_input_size = output_size

        return nn.Sequential(*layers)

    #def layer_make2d(self, net, args, options):
    #    return NoOp()

    #def layer_make3d(self, net, args, options):
    #    return NoOp()

    def layer_modulated_conv2d(self, net, args, options):
        channels = args[0]
        method = "conv"
        if len(args) > 1:
            method = args[1]
        upsample = method == "upsample"
        downsample = method == "downsample"

        demodulate = True
        if options.demodulate == False:
            demodulate = False

        filter = 3
        if options.filter:
            filter = options.filter

        input_channels = self.current_channels
        if options.input_channels:
            input_channels = options.input_channels

        result = ModulatedConv2d(input_channels, channels, filter, self.adaptive_instance_norm_size, upsample=upsample, demodulate=demodulate, downsample=downsample)

        if upsample:
            self.current_width *= 2
            self.current_height *= 2
        elif downsample:
            self.current_width //= 2
            self.current_height //= 2
        self.current_channels = channels
        self.current_input_size = self.current_channels * self.current_width * self.current_height
        return result


    def layer_blur(self, net, args, options):
        blur_kernel=[1, 3, 3, 1]
        kernel_size=3
        factor = 2
        p = (len(blur_kernel) - factor) - (kernel_size - 1)
        pad0 = (p + 1) // 2 + factor - 1
        pad1 = p // 2 + 1

        return Blur(blur_kernel, pad=(pad0, pad1), upsample_factor=factor)

    def layer_reshape(self, net, args, options):
        dims = [int(x) for x in args[0].split("*")]
        if len(dims) == 3:
            self.current_width = dims[0]
            self.current_height = dims[1]
            self.current_channels = dims[2]
            self.current_input_size = self.current_channels * self.current_width * self.current_height
            dims = [dims[2], dims[0], dims[1]]
        if len(dims) == 2:
            self.current_width = 1
            self.current_height = dims[0]
            self.current_channels = dims[1]
            self.current_input_size = self.current_channels * self.current_width * self.current_height
            dims = [dims[1], dims[0]]
        return Reshape(*dims)

    def layer_adaptive_avg_pool(self, net, args, options):
        print("adaptive start", self.current_width, self.current_height, self.current_channels)
        self.current_height //= 2
        self.current_width //= 2
        self.current_input_size = self.current_channels * self.current_width * self.current_height
        return nn.AdaptiveAvgPool2d([self.current_height, self.current_width])

    def layer_adaptive_avg_pool1d (self, net, args, options):
        self.current_height //= 2
        self.current_input_size = self.current_channels * self.current_width * self.current_height
        print("avg>", self.current_input_size, self.current_width, self.current_height, self.current_channels)
        return nn.AdaptiveAvgPool1d(self.current_height)

    def layer_avg_pool(self, net, args, options):
        self.current_height //= 2
        self.current_width //= 2
        self.current_input_size = self.current_channels * self.current_width * self.current_height
        return nn.AvgPool2d(2, 2)

    def layer_adaptive_avg_pool3d(self, net, args, options):
        self.current_height //= 2
        self.current_width //= 2
        self.current_frames = 4 #todo
        self.current_input_size = self.current_frames * self.current_channels * self.current_width * self.current_height
        return nn.AdaptiveAvgPool3d([self.current_frames, self.current_height, self.current_width])

    def layer_instance_norm(self, net, args, options):
        options = hc.Config(options)
        affine = True
        if options.affine == False:
            affine = False
        return nn.InstanceNorm2d(self.current_channels, affine=affine)

    def layer_instance_norm1d(self, net, args, options):
        options = hc.Config(options)
        affine = True
        if options.affine == False:
            affine = False
        return nn.InstanceNorm1d(self.current_channels, affine=affine)


    def layer_instance_norm3d(self, net, args, options):
        options = hc.Config(options)
        affine = True
        if options.affine == False:
            affine = False
        return nn.InstanceNorm3d(self.current_channels, affine=affine)


    def layer_initializer(self, net, args, options):
        print("init layer")
        layer = self.last_logit_layer.weight.data
        if args[0] == "uniform":
            a = float(args[1])
            b = float(args[2])
            nn.init.uniform_(layer, a, b)
        elif args[0] == "normal":
            mean = float(args[1])
            std = float(args[2])
            nn.init.normal_(layer, mean, std)
        elif args[0] == "constant":
            val = float(args[1])
            nn.init.constant_(layer, val)
        elif args[0] == "ones":
            nn.init.ones_(layer)
        elif args[0] == "zeros":
            nn.init.zeros_(layer)
        elif args[0] == "eye":
            nn.init.eye_(layer)
        elif args[0] == "dirac":
            nn.init.dirac_(layer)
        elif args[0] == "xavier_uniform":
            gain = nn.init.calculate_gain(options["gain"])
            nn.init.xavier_uniform_(layer, gain=gain)
        elif args[0] == "xavier_normal":
            gain = nn.init.calculate_gain(options["gain"])
            nn.init.xavier_normal_(layer, gain=gain)
        elif args[0] == "kaiming_uniform":
            a = 0 #TODO wrong
            nn.init.kaiming_uniform_(layer, mode="fan_in", nonlinearity=options["gain"])
        elif args[0] == "kaiming_normal":
            a = 0 #TODO wrong
            nn.init.kaiming_normal_(layer, mode=(options.mode or "fan_in"), nonlinearity=options["gain"])
        elif args[0] == "orthogonal":
            if "gain" in options:
                gain = nn.init.calculate_gain(options["gain"])
            else:
                gain = 1
            nn.init.orthogonal_(layer, gain=gain)
        else:
            print("Warning: No initializer found for " + args[0])
        if "gain" in options:
            layer.mul_(nn.init.calculate_gain(options["gain"]))
        return NoOp()

    def layer_batch_norm(self, net, args, options):
        return nn.BatchNorm2d(self.current_channels)

    def layer_batch_norm1d(self, net, args, options):
        return nn.BatchNorm1d(self.current_input_size)

    def get_conv_options(self, config, options):
        stride = options.stride or self.ops.config_option("stride", [1,1])
        fltr = options.filter or self.ops.config_option("filter", [3,3])
        avg_pool = options.avg_pool or self.ops.config_option("avg_pool", [1,1])

        if type(stride) != type([]):
            stride = [stride, stride]

        if type(avg_pool) != type([]):
            avg_pool = [avg_pool, avg_pool]

        if type(fltr) != type([]):
            fltr = [fltr, fltr]
        return stride, fltr, avg_pool


    def layer_deconv(self, net, args, options):
        channels = args[0]
        options = hc.Config(options)
        filter = 4 #TODO
        stride = 2
        padding = 1
        layers = [nn.ConvTranspose2d(options.input_channels or self.current_channels, channels, 4, 2, 1)]
        self.last_logit_layer = layers[0]
        self.current_channels = channels
        self.current_width = self.current_width * 2 #TODO
        self.current_height = self.current_height * 2 #TODO
        self.current_input_size = self.current_channels * self.current_width * self.current_height
        return nn.Sequential(*layers)

    def layer_pad(self, net, args, options):
        options = hc.Config(options)

        return nn.ZeroPad2d((args[0], args[1], args[2], args[3]))

    def layer_pixel_norm(self, net, args, options):
        return PixelNorm()

    def layer_upsample(self, net, args, options):
        w = options.w or self.current_width * 2
        h = options.h or self.current_height * 2
        result = nn.Upsample((h, w), mode="bilinear")
        self.current_width = self.current_width * 2 #TODO
        self.current_height = self.current_height * 2 #TODO
        return result

    def layer_resize_conv(self, net, args, options):
        options = hc.Config(options)
        channels = args[0]

        w = options.w or self.current_width * 2
        h = options.h or self.current_height * 2
        layers = [nn.Upsample((h, w), mode="bilinear"),
                nn.Conv2d(options.input_channels or self.current_channels, channels, options.filter or 3, 1, 1)]
        self.last_logit_layer = layers[-1]
        self.current_channels = channels
        self.current_width = self.current_width * 2 #TODO
        self.current_height = self.current_height * 2 #TODO
        return nn.Sequential(*layers)

    def layer_resize_conv1d(self, net, args, options):
        options = hc.Config(options)
        channels = args[0]

        h = options.h or self.current_height * 2
        layers = [nn.Upsample((h)),
                nn.Conv1d(options.input_channels or self.current_channels, channels, options.filter or 3, 1, 1)]
        self.last_logit_layer = layers[-1]
        self.current_channels = channels
        self.current_height = self.current_height * 2 #TODO
        print("Resize", self.current_height, self.current_channels)
        return nn.Sequential(*layers)

    def layer_split(self, net, args, options):
        options = hc.Config(options)
        split_size = args[0]
        select = args[1]
        dim = -1
        if options.dim:
            dim = options.dim
        #TODO better validation
        #TODO increase dim options
        if dim == -1:
            self.current_channels = split_size
            if (select + 1) * split_size > self.current_channels:
                self.current_channels = self.current_channels % split_size
            self.current_input_size = self.current_channels * self.current_width * self.current_height
        return NoOp()

    def layer_subpixel(self, net, args, options):
        options = hc.Config(options)
        channels = args[0]

        layers = [nn.Conv2d(options.input_channels or self.current_channels, channels*4, options.filter or 3, 1, 1), nn.PixelShuffle(2)]
        self.last_logit_layer = layers[0]
        self.current_width = self.current_width * 2 #TODO
        self.current_height = self.current_height * 2 #TODO
        self.current_channels = channels
        self.current_input_size = self.current_channels * self.current_width * self.current_height
        return nn.Sequential(*layers)

    def layer_latent(self, net, args, options):
        self.current_input_size = self.gan.latent.current_input_size
        return NoOp()

    def layer_layer(self, net, args, options):
        if args[0] in self.layer_sizes:
            self.current_width, self.current_height, self.current_channels = self.layer_sizes[args[0]]
            self.current_input_size = self.current_channels * self.current_width * self.current_height
        return NoOp()

    def layer_vae(self, net, args, options):
        self.vae = Variational(self.current_channels)
        return self.vae

    def layer_add(self, net, args, options):
        options = hc.Config(options)
        if args[0] == 'noise':
            return LearnedNoise()

    def layer_norm(self, net, args, options):
        affine = True
        if options.affine == False:
            affine = False

        return nn.LayerNorm([self.current_channels, self.current_height, self.current_width], elementwise_affine=affine)

    def layer_concat(self, net, args, options):
        options = hc.Config(options)
        if args[0] == 'noise':
            print("Concat noise!")
            self.current_channels *= 2
            return NoOp()
        elif args[0] == 'layer':
            return NoOp()
        else:
            print("Got: ", args[0])
            print("Warning: only 'concat noise' and 'concat layer' is supported for now.")

    #def layer_concat3d(self, net, args, options):
    #    options = hc.Config(options)
    #    if args[0] == 'noise':
    #        print("Concat noise!")
    #        return NoOp()
    #    elif args[0] == 'layer':
    #        return NoOp()
    #    else:
    #        print("Got: ", args[0])
    #        print("Warning: only 'concat noise' and 'concat layer' is supported for now.")

    def layer_adaptive_instance_norm(self, net, args, options):
        return AdaptiveInstanceNorm(self.adaptive_instance_norm_size, self.current_channels)

    def forward(self, input, context={}):
        self.context = context
        for module, parsed in zip(self.net, self.parsed_layers):
            options = parsed.parsed_options
            args = parsed.args
            layer_name = parsed.layer_name
            name = options.name
            if layer_name == "adaptive_instance_norm":
                input = module(input, self.context['w'])
            elif layer_name == "split":
                input = torch.split(input, args[0], options.dim or -1)[args[1]]
            elif layer_name == "concat":
                if args[0] == "layer":
                    input = torch.cat((input, self.context[args[1]]), dim=1)
                elif args[0] == "noise":
                    input = torch.cat((input, torch.randn_like(input)), dim=1)
            # elif layer_name == "concat3d":
            #    if args[0] == "layer":
            #        input = torch.cat((input, self.context[args[1]]), dim=2)
            #    elif args[0] == "noise":
            #        input = torch.cat((input, torch.randn_like(input)*0.01), dim=2)
            # elif layer_name == "make2d":
            #     input = torch.squeeze(input, dim=2) #TODO only 3d -> 2d
            # elif layer_name == "make3d":
            #     input = input[:,:,None,:,:] #TODO only 2d -> 3d

            elif layer_name == "layer":
                input = self.context[args[0]]
            elif layer_name == "latent":
                input = self.gan.latent.sample()
            elif layer_name == "modulated_conv2d":
                input = module(input, self.context['w'])
            else:
                input = module(input)
            if name is not None:
                self.context[name] = input
        self.sample = input
        return input
