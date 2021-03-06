import torch
import hyperchamber as hc
import numpy as np
import inspect
from operator import itemgetter
from hypergan.train_hooks.base_train_hook import BaseTrainHook
from torch.nn.parameter import Parameter
from torch.autograd import Variable
from torch.autograd import grad as torch_grad

class AdversarialNormTrainHook(BaseTrainHook):
    def __init__(self, gan=None, config=None):
        super().__init__(config=config, gan=gan)
        self.d_loss = None
        self.g_loss = None
        if self.config.gamma is not None:
            self.gamma = self.config.gamma#torch.Tensor([self.config.gamma]).float()[0].cuda()#self.gan.configurable_param(self.config.gamma or 1.0)
        if self.config.gammas is not None:
            self.gammas = [
                        self.config.gammas[0],#torch.Tensor([self.config.gammas[0]]).float()[0].cuda(),#self.gan.configurable_param(self.config.gamma or 1.0)
                        self.config.gammas[1]#torch.Tensor([self.config.gammas[1]]).float()[0].cuda()#self.gan.configurable_param(self.config.gamma or 1.0)
                    ]
        self.relu = torch.nn.ReLU()
        self.target = [Parameter(x, requires_grad=True) for x in self.gan.discriminator_real_inputs()]
        self.x_mod_target = torch.zeros_like(self.target[0])
        self.g_mod_target = torch.zeros_like(self.target[0])
        self.loss = self.gan.create_component("loss")

    def forward(self, d_loss, g_loss):
        if self.config.mode == "real" or self.config.mode is None:
            for target, data in zip(self.target, self.gan.discriminator_real_inputs()):
                target.data = data.clone()
            d_fake = self.gan.d_fake
            d_real = self.gan.forward_discriminator(self.target)
            loss, _, mod_target = self.regularize_adversarial_norm(d_fake, d_real, self.target)
            norm = (-((mod_target[0] - self.gan.discriminator_real_inputs()[0])**2)).mean()
            for mt, t in zip(mod_target[1:], self.gan.discriminator_real_inputs()[1:]):
                norm += (-((mt - t) ** 2)).mean()
            if self.config.forward_discriminator:
                dadv = self.gan.forward_discriminator(mod_target)
                norm += (-((dadv - self.gan.d_real) ** 2)).mean()
        elif self.config.mode == "fake":
            norm = None
            for d_fake_in in self.gan.discriminator_fake_inputs():
                for target, data in zip(self.target, d_fake_in):
                    target.data = data.clone()
                d_fake = self.gan.forward_discriminator(self.target)
                d_real = self.gan.d_real
                loss, norm, mod_target = self.regularize_adversarial_norm(d_real, d_fake, self.target)
                for mt, t in zip(mod_target, d_fake_in):
                    if norm == None:
                        norm = (-((mt - t) ** 2)).mean()
                    else:
                        norm += (-((mt - t) ** 2)).mean()
                if self.config.forward_discriminator:
                    dadv = self.gan.forward_discriminator(mod_target)
                    norm += (-((dadv - self.gan.d_fake) ** 2)).mean()
            norm /= len(self.gan.discriminator_fake_inputs())
        elif self.config.mode == "autoenc":
            d_fake_in = self.gan.discriminator_fake_inputs()[0]
            for target, data in zip(self.target, d_fake_in):
                target.data = data.clone()
            d_fake = self.gan.forward_discriminator(self.target)
            d_real = self.gan.d_real
            loss, norm, mod_target = self.regularize_adversarial_norm(d_real, d_fake, self.target)
            d_real = self.gan.forward_discriminator(mod_target)
            d_fake = self.gan.forward_discriminator(d_fake_in)
            d_l, g_l = self.loss.forward(d_real, d_fake)
            if self.config.real is None:
                return (self.config.gammas[0]*d_l), (self.config.gammas[1]*g_l)
            else:
                for target, data in zip(self.target, mod_target):
                    target.data = data.clone()
                loss, _, mod2_target = self.regularize_adversarial_norm(d_fake, d_real, self.target)
                norm = (-((mod2_target[0] - mod_target[0])**2)).mean()
                for mt, t in zip(mod2_target[1:], mod_target):
                    norm += (-((mt - t) ** 2)).mean()
                self.gan.add_metric('r_d', norm)
                return (self.config.real_gamma * norm + self.config.gammas[0]*d_l), (self.config.gammas[1]*g_l)
        elif self.config.mode == "autoenc_x":
            d_real_in = self.gan.discriminator_real_inputs()
            for target, data in zip(self.target, d_real_in):
                target.data = data.clone()
            d_fake = self.gan.d_fake
            d_real = self.gan.forward_discriminator(self.target)
            loss, norm, mod_target = self.regularize_adversarial_norm(d_real, d_fake, self.target)
            d_fake = self.gan.forward_discriminator(mod_target)
            d_real = self.gan.forward_discriminator(d_real_in)
            d_l, g_l = self.loss.forward(d_real, d_fake)
            return (self.config.gammas[0]*d_l), (self.config.gammas[1]*g_l)

        if self.config.second_order is not None:
            for target, data in zip(self.target, mod_target):
                target.data = data.clone()
            loss, _, mod2_target = self.regularize_adversarial_norm(d_fake, d_real, self.target)
            norm = (-((mod2_target[0] - mod_target[0])**2)).mean()
            for mt, t in zip(mod2_target[1:], mod_target):
                norm += (-((mt - t) ** 2)).mean()

        if self.config.loss:
          if "g" in self.config.loss:
              self.g_loss = self.gamma * norm.mean()
              self.gan.add_metric('an_g', self.g_loss)
          if "d" in self.config.loss:
              self.d_loss = self.gamma * norm.mean()
              self.gan.add_metric('an_d', self.d_loss)
          if "dg" in self.config.loss:
              self.d_loss = self.gammas[0] * norm.mean()
              self.gan.add_metric('an_d', self.d_loss)
              self.g_loss = self.gammas[1] * norm.mean()
              self.gan.add_metric('an_g', self.g_loss)
        else:
            self.d_loss = self.gamma * norm.mean()
            self.gan.add_metric('an_d', self.d_loss)

        return [self.d_loss, self.g_loss]

    def regularize_adversarial_norm(self, d1_logits, d2_logits, target):
        loss = self.forward_adversarial_norm(d1_logits, d2_logits)

        d1_grads = torch_grad(outputs=loss, inputs=target, retain_graph=True, create_graph=True)
        mod_target = [_d1 + _t for _d1, _t in zip(d1_grads, target)]

        return loss, None, mod_target

    def forward_adversarial_norm(self, d_real, d_fake):
        #return (torch.sign(d_real-d_fake)*((d_real - d_fake)**2)).mean()
        return ((d_real - d_fake)**2).mean()
        #return 0.5 * (self.dist(d_real,d_fake) + self.dist(d_fake, d_real)).sum()
