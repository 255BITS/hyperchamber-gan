import tensorflow as tf
import hyperchamber as hc
import numpy as np

TINY=1e-12

def config():
  selector = hc.Selector()
  selector.set('create', create)
  selector.set('z', [20,40,80])
  selector.set('min', -1)
  selector.set('max', 1)

  selector.set('projections', [[linear, gaussian, sphere]])
  selector.set('periods', 4)

  return selector.random_config()

def create(config, gan):
  zs = []
  z_base = tf.random_uniform([gan.config.batch_size, config.z],config.min, config.max,dtype=gan.config.dtype)
  for projection in config.projections:
      zs.append(projection(config, gan, z_base))
  zs = tf.concat(axis=1, values=zs)
  return zs, z_base

def linear(config, gan, net):
  return net

def sphere(config, gan, net):
  net = gaussian(config, gan, net)
  spherenet = tf.square(net)
  spherenet = tf.reduce_sum(spherenet, 1)
  lam = tf.sqrt(spherenet)
  return net/tf.reshape(lam,[int(lam.get_shape()[0]), 1])

# creates normal distribution from uniform values https://en.wikipedia.org/wiki/Box%E2%80%93Muller_transform
def gaussian(config, gan, z):
  z_dim = config.z
  z = (z + 1) / 2

  za = tf.slice(z, [0,0], [gan.config.batch_size, z_dim//2])
  zb = tf.slice(z, [0,z_dim//2], [gan.config.batch_size, z_dim//2])

  pi = np.pi
  ra = tf.sqrt(-2 * tf.log(za+TINY))*tf.cos(2*pi*zb)
  rb = tf.sqrt(-2 * tf.log(za+TINY))*tf.sin(2*pi*zb)

  return tf.reshape(tf.concat(axis=1, values=[ra, rb]), z.get_shape())


def periodic(config, gan, net):
  return periodic_triangle_waveform(net, config.periods)

def periodic_gaussian(config, gan, net):
  net = periodic_triangle_waveform(net, config.periods)
  return gaussian(config, gan, net)


def periodic_triangle_waveform(z, p):
  return 2.0 / np.pi * tf.asin(tf.sin(2*np.pi*z/p))

