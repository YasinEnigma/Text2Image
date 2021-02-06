""" Module containing custom layers """

import torch as th


# extending Conv2D and Deconv2D layers for equalized learning rate logic
class _equalized_conv2d(th.nn.Module):
    """ conv2d with the concept of equalized learning rate """

    def __init__(self, c_in, c_out, k_size, stride=1, pad=0, initializer='kaiming', bias=True):
        """
        constructor for the class
        :param c_in: input channels
        :param c_out:  output channels
        :param k_size: kernel size (h, w) should be a tuple or a single integer
        :param stride: stride for conv
        :param pad: padding
        :param initializer: initializer. one of kaiming or xavier
        :param bias: whether to use bias or not
        """
        super(_equalized_conv2d, self).__init__()
        self.conv = th.nn.Conv2d(c_in, c_out, k_size, stride, pad, bias=False)
        if initializer == 'kaiming':
            th.nn.init.kaiming_normal_(self.conv.weight, a=th.nn.init.calculate_gain('conv2d'))
        elif initializer == 'xavier':
            th.nn.init.xavier_normal_(self.conv.weight)

        self.use_bias = bias

        if self.use_bias:
            self.bias = th.nn.Parameter(th.FloatTensor(c_out).fill_(0))
        self.scale = (th.mean(self.conv.weight.data ** 2)) ** 0.5
        self.conv.weight.data.copy_(self.conv.weight.data / self.scale)

    def forward(self, x):
        """
        forward pass of the network
        :param x: input
        :return: y => output
        """
        try:
            dev_scale = self.scale.to(x.get_device())
        except RuntimeError:
            dev_scale = self.scale
        x = self.conv(x.mul(dev_scale))
        if self.use_bias:
            return x + self.bias.view(1, -1, 1, 1).expand_as(x)
        return x


class _equalized_deconv2d(th.nn.Module):
    """ Transpose convolution using the equalized learning rate """

    def __init__(self, c_in, c_out, k_size, stride=1, pad=0, initializer='kaiming', bias=True):
        """
        constructor for the class
        :param c_in: input channels
        :param c_out: output channels
        :param k_size: kernel size
        :param stride: stride for convolution transpose
        :param pad: padding
        :param initializer: initializer. one of kaiming or xavier
        :param bias: whether to use bias or not
        """
        super(_equalized_deconv2d, self).__init__()
        self.deconv = th.nn.ConvTranspose2d(c_in, c_out, k_size, stride, pad, bias=False)
        if initializer == 'kaiming':
            th.nn.init.kaiming_normal_(self.deconv.weight, a=th.nn.init.calculate_gain('conv2d'))
        elif initializer == 'xavier':
            th.nn.init.xavier_normal_(self.deconv.weight)

        self.use_bias = bias

        if self.use_bias:
            self.bias = th.nn.Parameter(th.FloatTensor(c_out).fill_(0))
        self.scale = (th.mean(self.deconv.weight.data ** 2)) ** 0.5
        self.deconv.weight.data.copy_(self.deconv.weight.data / self.scale)

    def forward(self, x):
        """
        forward pass of the layer
        :param x: input
        :return: y => output
        """
        try:
            dev_scale = self.scale.to(x.get_device())
        except RuntimeError:
            dev_scale = self.scale

        x = self.deconv(x.mul(dev_scale))
        if self.use_bias:
            return x + self.bias.view(1, -1, 1, 1).expand_as(x)
        return x


class _equalized_linear(th.nn.Module):
    """ Linear layer using equalized learning rate
        Args:
            :param c_in: number of input channels
            :param c_out: number of output channels
            :param bias: whether to use bias with the linear layer
    """

    def __init__(self, c_in, c_out, bias=True):
        """
        Linear layer modified for equalized learning rate
        """
        from numpy import sqrt

        super(_equalized_linear, self).__init__()

        self.weight = th.nn.Parameter(th.nn.init.normal_(
            th.empty(c_out, c_in)
        ))

        self.use_bias = bias

        if self.use_bias:
            self.bias = th.nn.Parameter(th.FloatTensor(c_out).fill_(0))

        fan_in = c_in
        self.scale = sqrt(2) / sqrt(fan_in)

    def forward(self, x):
        """
        forward pass of the layer
        :param x: input
        :return: y => output
        """
        from torch.nn.functional import linear
        return linear(x, self.weight * self.scale,
                      self.bias if self.use_bias else None)


# ----------------------------------------------------------------------------
# Pixelwise feature vector normalization.
# reference: https://github.com/tkarras/progressive_growing_of_gans/blob/master/networks.py#L120
# ----------------------------------------------------------------------------
class PixelwiseNorm(th.nn.Module):
    def __init__(self):
        super(PixelwiseNorm, self).__init__()

    def forward(self, x, alpha=1e-8):
        """
        forward pass of the module
        :param x: input activations volume
        :param alpha: small number for numerical stability
        :return: y => pixel normalized activations
        """
        y = x.pow(2.).mean(dim=1, keepdim=True).add(alpha).sqrt()  # [N1HW]
        y = x / y  # normalize the input x volume
        return y


# ==========================================================
# Layers required for Building The generator and
# discriminator
# ==========================================================
class GenInitialBlock(th.nn.Module):
    """ Module implementing the initial block of the input """

    def __init__(self, in_channels, use_eql):
        """
        constructor for the inner class
        :param in_channels: number of input channels to the block
        :param use_eql: whether to use equalized learning rate
        """
        from torch.nn import LeakyReLU

        super(GenInitialBlock, self).__init__()

        if use_eql:
            self.conv_1 = _equalized_deconv2d(in_channels, in_channels, (4, 4), bias=True)
            self.conv_2 = _equalized_conv2d(in_channels, in_channels, (3, 3),
                                            pad=1, bias=True)

        else:
            from torch.nn import Conv2d, ConvTranspose2d
            self.conv_1 = ConvTranspose2d(in_channels, in_channels, (4, 4), bias=True)
            self.conv_2 = Conv2d(in_channels, in_channels, (3, 3), padding=1, bias=True)

        # Pixelwise feature vector normalization operation
        self.pixNorm = PixelwiseNorm()

        # leaky_relu:
        self.lrelu = LeakyReLU(0.2)

    def forward(self, x):
        """
        forward pass of the block
        :param x: input to the module
        :return: y => output
        """
        # convert the tensor shape:
        y = th.unsqueeze(th.unsqueeze(x, -1), -1)

        # perform the forward computations:
        y = self.lrelu(self.conv_1(y))
        y = self.lrelu(self.conv_2(y))

        # apply pixel norm
        y = self.pixNorm(y)

        return y


class GenGeneralConvBlock(th.nn.Module):
    """ Module implementing a general convolutional block """

    def __init__(self, in_channels, out_channels, use_eql):
        """
        constructor for the class
        :param in_channels: number of input channels to the block
        :param out_channels: number of output channels required
        :param use_eql: whether to use equalized learning rate
        """
        from torch.nn import LeakyReLU, Upsample

        super(GenGeneralConvBlock, self).__init__()

        self.upsample = Upsample(scale_factor=2)

        if use_eql:
            self.conv_1 = _equalized_conv2d(in_channels, out_channels, (3, 3),
                                            pad=1, bias=True)
            self.conv_2 = _equalized_conv2d(out_channels, out_channels, (3, 3),
                                            pad=1, bias=True)
        else:
            from torch.nn import Conv2d
            self.conv_1 = Conv2d(in_channels, out_channels, (3, 3),
                                 padding=1, bias=True)
            self.conv_2 = Conv2d(out_channels, out_channels, (3, 3),
                                 padding=1, bias=True)

        # Pixelwise feature vector normalization operation
        self.pixNorm = PixelwiseNorm()

        # leaky_relu:
        self.lrelu = LeakyReLU(0.2)

    def forward(self, x):
        """
        forward pass of the block
        :param x: input
        :return: y => output
        """
        y = self.upsample(x)
        y = self.pixNorm(self.lrelu(self.conv_1(y)))
        y = self.pixNorm(self.lrelu(self.conv_2(y)))

        return y


# function to calculate the Exponential moving averages for the Generator weights
# This function updates the exponential average weights based on the current training
def update_average(model_tgt, model_src, beta):
    """
    update the model_target using exponential moving averages
    :param model_tgt: target model
    :param model_src: source model
    :param beta: value of decay beta
    :return: None (updates the target model)
    """

    # utility function for toggling the gradient requirements of the models
    def toggle_grad(model, requires_grad):
        for p in model.parameters():
            p.requires_grad_(requires_grad)

    # turn off gradient calculation
    toggle_grad(model_tgt, False)
    toggle_grad(model_src, False)

    param_dict_src = dict(model_src.named_parameters())

    for p_name, p_tgt in model_tgt.named_parameters():
        p_src = param_dict_src[p_name]
        assert (p_src is not p_tgt)
        p_tgt.copy_(beta * p_tgt + (1. - beta) * p_src)

    # turn back on the gradient calculation
    toggle_grad(model_tgt, True)
    toggle_grad(model_src, True)


class MinibatchStdDev(th.nn.Module):
    """
    Minibatch standard deviation layer for the discriminator
    """

    def __init__(self):
        """
        derived class constructor
        """
        super(MinibatchStdDev, self).__init__()

    def forward(self, x, alpha=1e-8):
        """
        forward pass of the layer
        :param x: input activation volume
        :param alpha: small number for numerical stability
        :return: y => x appended with standard deviation constant map
        """
        batch_size, _, height, width = x.shape

        # [B x C x H x W] Subtract mean over batch.
        y = x - x.mean(dim=0, keepdim=True)

        # [1 x C x H x W]  Calc standard deviation over batch
        y = th.sqrt(y.pow(2.).mean(dim=0, keepdim=False) + alpha)

        # [1]  Take average over feature_maps and pixels.
        y = y.mean().view(1, 1, 1, 1)

        # [B x 1 x H x W]  Replicate over group and pixels.
        y = y.repeat(batch_size, 1, height, width)

        # [B x C x H x W]  Append as new feature_map.
        y = th.cat([x, y], 1)

        # return the computed values:
        return y


class DisFinalBlock(th.nn.Module):
    """ Final block for the Discriminator """

    def __init__(self, in_channels, use_eql):
        """
        constructor of the class
        :param in_channels: number of input channels
        :param use_eql: whether to use equalized learning rate
        """
        from torch.nn import LeakyReLU

        super(DisFinalBlock, self).__init__()

        # declare the required modules for forward pass
        self.batch_discriminator = MinibatchStdDev()
        if use_eql:
            self.conv_1 = _equalized_conv2d(in_channels + 1, in_channels, (3, 3), pad=1, bias=True)
            self.conv_2 = _equalized_conv2d(in_channels, in_channels, (4, 4), bias=True)
            # final conv layer emulates a fully connected layer
            self.conv_3 = _equalized_conv2d(in_channels, 1, (1, 1), bias=True)
        else:
            from torch.nn import Conv2d
            self.conv_1 = Conv2d(in_channels + 1, in_channels, (3, 3), padding=1, bias=True)
            self.conv_2 = Conv2d(in_channels, in_channels, (4, 4), bias=True)
            # final conv layer emulates a fully connected layer
            self.conv_3 = Conv2d(in_channels, 1, (1, 1), bias=True)

        # leaky_relu:
        self.lrelu = LeakyReLU(0.2)

    def forward(self, x):
        """
        forward pass of the FinalBlock
        :param x: input
        :return: y => output
        """
        # minibatch_std_dev layer
        y = self.batch_discriminator(x)

        # define the computations
        y = self.lrelu(self.conv_1(y))
        y = self.lrelu(self.conv_2(y))

        # fully connected layer
        y = self.conv_3(y)  # This layer has linear activation

        # flatten the output raw discriminator scores
        return y.view(-1)


class ConDisFinalBlock(th.nn.Module):
    """ Final block for the Conditional Discriminator """

    def __init__(self, in_channels, in_latent_size, out_latent_size, use_eql):
        """
        constructor of the class
        :param in_channels: number of input channels
        :param in_latent_size: size of the input latent vectors
        :param out_latent_size: size of the transformed latent vectors
        :param use_eql: whether to use equalized learning rate
        """
        from torch.nn import LeakyReLU

        super(ConDisFinalBlock, self).__init__()

        # declare the required modules for forward pass
        self.batch_discriminator = MinibatchStdDev()
        if use_eql:
            self.compressor = _equalized_linear(c_in=in_latent_size, c_out=out_latent_size)
            self.conv_1 = _equalized_conv2d(in_channels + 1, in_channels, (3, 3), pad=1, bias=True)
            self.conv_2 = _equalized_conv2d(in_channels + out_latent_size,
                                            in_channels, (1, 1), bias=True)
            self.conv_3 = _equalized_conv2d(in_channels, in_channels, (4, 4), bias=True)
            # final conv layer emulates a fully connected layer
            self.conv_4 = _equalized_conv2d(in_channels, 1, (1, 1), bias=True)
        else:
            from torch.nn import Conv2d, Linear
            self.compressor = Linear(in_features=in_latent_size,
                                     out_features=out_latent_size, bias=True)
            self.conv_1 = Conv2d(in_channels + 1, in_channels, (3, 3), padding=1, bias=True)
            self.conv_2 = Conv2d(in_channels + out_latent_size,
                                 in_channels, (1, 1), bias=True)
            self.conv_3 = Conv2d(in_channels, in_channels, (4, 4), bias=True)
            # final conv layer emulates a fully connected layer
            self.conv_4 = Conv2d(in_channels, 1, (1, 1), bias=True)

        # leaky_relu:
        self.lrelu = LeakyReLU(0.2)

    def forward(self, x, latent_vector):
        """
        forward pass of the FinalBlock
        :param x: input
        :param latent_vector: latent vector for conditional discrimination
        :return: y => output
        """
        # minibatch_std_dev layer
        y = self.batch_discriminator(x)

        # define the computations
        y = self.lrelu(self.conv_1(y))
        # apply the latent vector here:
        compressed_latent_vector = self.compressor(latent_vector)
        cat = th.unsqueeze(th.unsqueeze(compressed_latent_vector, -1), -1)
        cat = cat.expand(
            compressed_latent_vector.shape[0],
            compressed_latent_vector.shape[1],
            y.shape[2],
            y.shape[3]
        )
        y = th.cat((y, cat), dim=1)

        y = self.lrelu(self.conv_2(y))
        y = self.lrelu(self.conv_3(y))

        # fully connected layer
        y = self.conv_4(y)  # This layer has linear activation

        # flatten the output raw discriminator scores
        return y.view(-1)


class DisGeneralConvBlock(th.nn.Module):
    """ General block in the discriminator  """

    def __init__(self, in_channels, out_channels, use_eql):
        """
        constructor of the class
        :param in_channels: number of input channels
        :param out_channels: number of output channels
        :param use_eql: whether to use equalized learning rate
        """
        from torch.nn import AvgPool2d, LeakyReLU

        super(DisGeneralConvBlock, self).__init__()

        if use_eql:
            self.conv_1 = _equalized_conv2d(in_channels, in_channels, (3, 3), pad=1, bias=True)
            self.conv_2 = _equalized_conv2d(in_channels, out_channels, (3, 3), pad=1, bias=True)
        else:
            from torch.nn import Conv2d
            self.conv_1 = Conv2d(in_channels, in_channels, (3, 3), padding=1, bias=True)
            self.conv_2 = Conv2d(in_channels, out_channels, (3, 3), padding=1, bias=True)

        self.downSampler = AvgPool2d(2)

        # leaky_relu:
        self.lrelu = LeakyReLU(0.2)

    def forward(self, x):
        """
        forward pass of the module
        :param x: input
        :return: y => output
        """
        # define the computations
        y = self.lrelu(self.conv_1(x))
        y = self.lrelu(self.conv_2(y))
        y = self.downSampler(y)

        return y
