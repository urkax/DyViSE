from argparse import ArgumentError
import os
import torch
import glog as log
from torch import nn

from utils.tools import load_pretrained_model, download_from_google_drive


class Resnet_facial(nn.Module):
    fc_scale = 7 * 7
    def __init__(self, init_weight, *args, **kwargs):
        super().__init__()
        default_config = {'layers': [3, 4, 14, 3],
                            'num_features': 512,
                            'inplanes': 64,
                            'fix_layers': all,
                            'load_init_weight': True}

        self.layers = default_config['layers']
        self.fix_layers = default_config['fix_layers']
        self.init_weight = init_weight
        self.load_init_weight = default_config['load_init_weight']
        self.fp16 = default_config.get('fp16', False)
        self.inplanes = default_config.get('inplanes', 64)
        self.dilation = default_config.get('dilation', 1)
        self.groups = default_config.get('groups', 1)
        self.dropout = default_config.get('dropout', 0)
        self.base_width = default_config.get('width_per_group', 64)
        self.num_features = default_config.get('num_features', 512)
        self.zero_init_residual = default_config.get('zero_init_residual', False)
        replace_stride_with_dilation = default_config.get('replace_stride_with_dilation', None)
        if replace_stride_with_dilation is None:
            replace_stride_with_dilation = [False, False, False]
        if len(replace_stride_with_dilation) != 3:
            raise ValueError("replace_stride_with_dilation should be None "
                             "or a 3-element tuple, got {}".format(replace_stride_with_dilation))

        self.conv1 = nn.Conv2d(3, self.inplanes, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(self.inplanes, eps=1e-05)
        self.prelu = nn.PReLU(self.inplanes)
        self.layer1 = self._make_layer(IBasicBlock, 64, self.layers[0], stride=2)
        self.layer2 = self._make_layer(IBasicBlock,
                                       128,
                                       self.layers[1],
                                       stride=2,
                                       dilate=replace_stride_with_dilation[0])
        self.layer3 = self._make_layer(IBasicBlock,
                                       256,
                                       self.layers[2],
                                       stride=2,
                                       dilate=replace_stride_with_dilation[1])
        self.layer4 = self._make_layer(IBasicBlock,
                                       512,
                                       self.layers[3],
                                       stride=2,
                                       dilate=replace_stride_with_dilation[2])
        self.bn2 = nn.BatchNorm2d(512 * IBasicBlock.expansion, eps=1e-05,)

        ############
        self.dropout = nn.Dropout(p=self.dropout, inplace=True)
        self.fc = nn.Linear(512 * IBasicBlock.expansion * self.fc_scale, self.num_features)
        self.features = nn.BatchNorm1d(self.num_features, eps=1e-05)
        nn.init.constant_(self.features.weight, 1.0)
        self.features.weight.requires_grad = False
        self._init_parameters()

    def _make_layer(self, block, planes, blocks, stride=1, dilate=False):
        downsample = None
        previous_dilation = self.dilation
        if dilate:
            self.dilation *= stride
            stride = 1
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                conv1x1(self.inplanes, planes * block.expansion, stride),
                nn.BatchNorm2d(planes * block.expansion, eps=1e-05, ),
            )
        layers = []
        layers.append(
            block(self.inplanes, planes, stride, downsample, self.groups,
                  self.base_width, previous_dilation))
        self.inplanes = planes * block.expansion
        for _ in range(1, blocks):
            layers.append(
                block(self.inplanes,
                      planes,
                      groups=self.groups,
                      base_width=self.base_width,
                      dilation=self.dilation))

        return nn.Sequential(*layers)

    def _init_parameters(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.normal_(m.weight, 0, 0.1)
            elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm)):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

        if self.zero_init_residual:
            for m in self.modules():
                if isinstance(m, IBasicBlock):
                    nn.init.constant_(m.bn2.weight, 0)
        
        if self.load_init_weight:
            if not os.path.exists(self.init_weight):
                raise ArgumentError('visualEncoder weight not exist')

            ckpt_state_dict = load_pretrained_model(self.init_weight, init=True)
            self.load_state_dict(ckpt_state_dict)

        if self.fix_layers == 'all':
            for p in self.parameters():
                p.requires_grad = False
                
    def forward(self, x):
        N, C, H, W = x.shape
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.prelu(x)
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)
        x = self.bn2(x) # N*S*D, C, H, W
        ###########
        x = torch.flatten(x, 1)
        x = self.dropout(x)
        x = self.fc(x)
        x = self.features(x)
        return x


class IBasicBlock(nn.Module):
    expansion = 1
    def __init__(self, inplanes, planes, stride=1, downsample=None,
                 groups=1, base_width=64, dilation=1):
        super(IBasicBlock, self).__init__()
        if groups != 1 or base_width != 64:
            raise ValueError('BasicBlock only supports groups=1 and base_width=64')
        if dilation > 1:
            raise NotImplementedError("Dilation > 1 not supported in BasicBlock")
        self.bn1 = nn.BatchNorm2d(inplanes, eps=1e-05,)
        self.conv1 = conv3x3(inplanes, planes)
        self.bn2 = nn.BatchNorm2d(planes, eps=1e-05,)
        self.prelu = nn.PReLU(planes)
        self.conv2 = conv3x3(planes, planes, stride)
        self.bn3 = nn.BatchNorm2d(planes, eps=1e-05,)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        identity = x
        out = self.bn1(x)
        out = self.conv1(out)
        out = self.bn2(out)
        out = self.prelu(out)
        out = self.conv2(out)
        out = self.bn3(out)
        if self.downsample is not None:
            identity = self.downsample(x)
        out += identity
        return out


def conv3x3(in_planes, out_planes, stride=1, groups=1, dilation=1):
    """3x3 convolution with padding"""
    return nn.Conv2d(in_planes,
                     out_planes,
                     kernel_size=3,
                     stride=stride,
                     padding=dilation,
                     groups=groups,
                     bias=False,
                     dilation=dilation)


def conv1x1(in_planes, out_planes, stride=1):
    """1x1 convolution"""
    return nn.Conv2d(in_planes,
                     out_planes,
                     kernel_size=1,
                     stride=stride,
                     bias=False)