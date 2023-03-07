import torch


class ConvBlock(torch.nn.Module):
    def __init__(self, input_size, output_size, kernel_size=3, stride=2, padding=1, activation='relu', batch_norm=True):
        super(ConvBlock, self).__init__()
        self.conv = torch.nn.Conv2d(input_size, output_size, kernel_size, stride, padding)
        self.batch_norm = batch_norm
        self.bn = torch.nn.InstanceNorm2d(output_size)
        self.activation = activation
        self.relu = torch.nn.ReLU(inplace=True)
        self.leaky_relu = torch.nn.LeakyReLU(0.2, inplace=True)
        self.tanh = torch.nn.Tanh()

        torch.nn.init.kaiming_uniform_(self.conv.weight, a=0, mode='fan_out', nonlinearity=activation)

    def forward(self, x):
        if self.batch_norm:
            out = self.bn(self.conv(x))
        else:
            out = self.conv(x)

        if self.activation == 'relu':
            return self.relu(out)
        elif self.activation == 'leaky_relu':
            return self.leaky_relu(out)
        elif self.activation == 'tanh':
            return self.tanh(out)
        elif self.activation == 'linear':
            return out


class DeconvBlock(torch.nn.Module):
    def __init__(self, input_size, output_size, kernel_size=3, stride=2, padding=1,
                 output_padding=1, activation='relu', batch_norm=True):

        super(DeconvBlock, self).__init__()
        self.deconv = torch.nn.ConvTranspose2d(input_size, output_size, kernel_size, stride, padding, output_padding)
        self.batch_norm = batch_norm
        self.bn = torch.nn.InstanceNorm2d(output_size)
        self.activation = activation
        self.relu = torch.nn.ReLU(inplace=True)

        torch.nn.init.kaiming_uniform_(self.deconv.weight, a=0, mode='fan_out', nonlinearity=activation)

    def forward(self, x):
        if self.batch_norm:
            out = self.bn(self.deconv(x))
        else:
            out = self.deconv(x)

        return self.relu(out)


class ResnetBlock(torch.nn.Module):
    def __init__(self, num_filter, kernel_size=3, stride=1, padding=0):
        super(ResnetBlock, self).__init__()
        conv1 = torch.nn.Conv2d(num_filter, num_filter, kernel_size, stride, padding)
        conv2 = torch.nn.Conv2d(num_filter, num_filter, kernel_size, stride, padding)
        bn = torch.nn.InstanceNorm2d(num_filter)
        relu = torch.nn.ReLU(inplace=True)
        pad = torch.nn.ReflectionPad2d(1)

        torch.nn.init.kaiming_uniform_(conv1.weight, a=0, mode='fan_out', nonlinearity='relu')
        torch.nn.init.kaiming_uniform_(conv2.weight, a=0, mode='fan_out', nonlinearity='relu')
        torch.nn.init.constant_(conv1.bias, 0)
        torch.nn.init.constant_(conv2.bias, 0)

        self.resnet_block = torch.nn.Sequential(
            pad,
            conv1,
            bn,
            relu,
            pad,
            conv2,
            bn,
            relu
        )

    def forward(self, x):
        return self.resnet_block(x) + x


class Generator(torch.nn.Module):
    def __init__(self, num_filter, num_resnet, input_dim=3, output_dim=3):
        super(Generator, self).__init__()

        # Mask encoder
        self.conv1dc = ConvBlock(input_dim * 2, input_dim, kernel_size=1, stride=1, padding=0, activation='linear',
                                 batch_norm=False)
        self.conv1dm = ConvBlock(input_dim * 2, input_dim, kernel_size=1, stride=1, padding=0, activation='linear',
                                 batch_norm=False)

        torch.nn.init.trunc_normal_(self.conv1dc.conv.weight, mean=1.0, std=0.02, a=0.995, b=1.005)
        torch.nn.init.trunc_normal_(self.conv1dm.conv.weight, mean=1.0, std=0.02, a=0.995, b=1.005)

        # Reflection padding
        self.pad = torch.nn.ReflectionPad2d(3)
        self.pad1 = torch.nn.ReflectionPad2d(1)

        # Encoder
        self.conv1 = ConvBlock(input_dim, num_filter, kernel_size=7, stride=1, padding=0)
        self.conv2 = ConvBlock(num_filter, num_filter * 2)
        num_filter *= 2
        self.conv3 = ConvBlock(num_filter, num_filter * 2)
        num_filter *= 2
        self.conv4 = ConvBlock(num_filter, num_filter * 2)
        num_filter *= 2

        # Resnet blocks
        self.resnet_blocks = []
        for i in range(num_resnet):
            self.resnet_blocks.append(ResnetBlock(num_filter))

        self.resnet_blocks = torch.nn.Sequential(*self.resnet_blocks)

        # Decoder
        self.deconv1 = DeconvBlock(num_filter, num_filter // 2)
        num_filter //= 2
        self.deconv2 = DeconvBlock(num_filter, num_filter // 2)
        num_filter //= 2
        self.deconv3 = DeconvBlock(num_filter, num_filter // 2)
        num_filter //= 2
        self.deconv4 = ConvBlock(num_filter, num_filter, kernel_size=7, stride=1, padding=0)
        self.final = ConvBlock(num_filter, output_dim, kernel_size=3, stride=1, padding=0,
                               activation='tanh', batch_norm=False)

    def forward(self, img, mask=None):
        # Mask encoder
        if mask is not None:
            inv_masked_img = torch.cat(((1 - mask) * img, (1 - mask).expand(img.size(0), -1, -1, -1)), 1)  # context
            img = torch.cat((mask*img, mask.expand(img.size(0), -1, -1, -1)), 1)  # mask

            img = self.conv1dc(img)
            inv_masked_img = self.conv1dm(inv_masked_img)

        enc1 = self.conv1(self.pad(img))  # (bs, num_filter, 128, 128)
        enc2 = self.conv2(enc1)  # (bs, num_filter * 2, 64, 64)
        enc3 = self.conv3(enc2)  # (bs, num_filter * 4, 32, 32)
        enc4 = self.conv4(enc3)  # (bs, num_filter * 8, 16, 16)

        # Resnet blocks
        res = self.resnet_blocks(enc4)

        # Decoder
        dec1 = self.deconv1(res + enc4)
        dec2 = self.deconv2(dec1 + enc3)
        dec3 = self.deconv3(dec2 + enc2)
        dec4 = self.deconv4(self.pad(dec3 + enc1))
        out = self.final(self.pad1(dec4))

        if mask is not None:
            # noinspection PyUnboundLocalVariable
            out = out + inv_masked_img

        return out


class Discriminator(torch.nn.Module):
    def __init__(self, num_filter, input_dim=3, output_dim=1):
        super(Discriminator, self).__init__()

        conv1 = ConvBlock(input_dim, num_filter, kernel_size=4, stride=2, padding=1,
                          activation='leaky_relu', batch_norm=False)

        conv2 = ConvBlock(num_filter, num_filter * 2, kernel_size=4, stride=2, padding=1, activation='leaky_relu')
        conv3 = ConvBlock(num_filter * 2, num_filter * 4, kernel_size=4, stride=2, padding=1, activation='leaky_relu')
        conv4 = ConvBlock(num_filter * 4, num_filter * 8, kernel_size=4, stride=1, padding=1, activation='leaky_relu')
        # conv5 = ConvBlock(num_filter * 8, num_filter * 8, kernel_size=4, stride=1, padding=1, activation='leaky_relu')
        # conv6 = ConvBlock(num_filter * 8, num_filter * 8, kernel_size=4, stride=1, padding=1, activation='leaky_relu')
        conv7 = ConvBlock(num_filter * 8, output_dim, kernel_size=4, stride=1, padding=1, activation='linear',
                          batch_norm=False)

        self.conv_blocks = torch.nn.Sequential(
            conv1,
            conv2,
            conv3,
            conv4,
            conv7
        )

    def forward(self, x):
        out = self.conv_blocks(x)
        return out

    def loss_fake(self, x):
        out = self.forward(x)
        out = torch.nn.functional.adaptive_max_pool2d(out, output_size=1).squeeze(0).squeeze(0)
        return out
