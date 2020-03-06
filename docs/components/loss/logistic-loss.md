# Logistic Loss

* Publication: [http://stylegan.xyz/paper](http://stylegan.xyz/paper)
* HG Source: [/losses/logistic_loss.py]](https://github.com/HyperGAN/HyperGAN/tree/pytorch/hypergan/losses/logistic_loss.py)

```python
d_loss = self.softplus(-d_real) + self.softplus(d_fake)
g_loss = self.softplus(-d_fake)
```

## examples

```javascript
{                                                                                       
  "class": "function:hypergan.losses.logistic_loss.LogisticLoss"
}
```

## options

| attribute | description | type |
| :--- | :--- | :--- |
| beta | https://pytorch.org/docs/stable/\_modules/torch/nn/modules/activation.html\#Softplus | float \(optional\) |
| threshold | https://pytorch.org/docs/stable/\_modules/torch/nn/modules/activation.html\#Softplus | float \(optional\) |
