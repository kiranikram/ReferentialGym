import math
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.utils.model_zoo as model_zoo

import torchvision
from torchvision import models
from torchvision.models.resnet import model_urls, BasicBlock


def retrieve_output_shape(input, model):
    xin = input.to(model.device)
    xout = model(xin).cpu()
    return xout.shape


def hasnan(tensor):
    if torch.isnan(tensor).max().item() == 1:
        return True
    return False

def handle_nan(layer, verbose=True):
    for name, param in layer._parameters.items():
        if param is None or param.data is None: continue
        nan_indices = torch.isnan(layer._parameters[name].data)
        if verbose and torch.any(nan_indices).item(): print("WARNING: NaN found in {} of {}.".format(name, layer))
        layer._parameters[name].data[nan_indices] = 0
        if param.grad is None: continue
        nan_indices = torch.isnan(layer._parameters[name].grad.data)
        if verbose and torch.any(nan_indices).item(): print("WARNING: NaN found in the GRADIENT of {} of {}.".format(name, layer))
        layer._parameters[name].grad.data[nan_indices] = 0
        
def layer_init(layer, w_scale=1.0):
    for name, param in layer._parameters.items():
        if param is None or param.data is None: continue
        if 'bias' in name:
            #layer._parameters[name].data.fill_(0.0)
            layer._parameters[name].data.uniform_(-0.08,0.08)
        else:
            #nn.init.orthogonal_(layer._parameters[name].data)
            '''
            fanIn = param.size(0)
            fanOut = param.size(1)

            factor = math.sqrt(2.0/(fanIn + fanOut))
            weight = torch.randn(fanIn, fanOut) * factor
            layer._parameters[name].data.copy_(weight)
            '''
            
            '''
            layer._parameters[name].data.uniform_(-0.08,0.08)
            layer._parameters[name].data.mul_(w_scale)
            '''
            if len(layer._parameters[name].size()) > 1:
                nn.init.kaiming_normal_(layer._parameters[name], mode="fan_out", nonlinearity='leaky_relu')
            
    '''
    if hasattr(layer,"weight"):    
        #nn.init.orthogonal_(layer.weight.data)
        layer.weight.data.uniform_(-0.08,0.08)
        layer.weight.data.mul_(w_scale)
        if hasattr(layer,"bias") and layer.bias is not None:    
            #nn.init.constant_(layer.bias.data, 0)
            layer.bias.data.uniform_(-0.08,0.08)
        
    if hasattr(layer,"weight_ih"):
        #nn.init.orthogonal_(layer.weight_ih.data)
        layer.weight.data.uniform_(-0.08,0.08)
        layer.weight_ih.data.mul_(w_scale)
        if hasattr(layer,"bias_ih"):    
            #nn.init.constant_(layer.bias_ih.data, 0)
            layer.bias.data.uniform_(-0.08,0.08)
        
    if hasattr(layer,"weight_hh"):    
        #nn.init.orthogonal_(layer.weight_hh.data)
        layer.weight.data.uniform_(-0.08,0.08)
        layer.weight_hh.data.mul_(w_scale)
        if hasattr(layer,"bias_hh"):    
            #nn.init.constant_(layer.bias_hh.data, 0)
            layer.bias.data.uniform_(-0.08,0.08)
    '''

    return layer


class FCBody(nn.Module):
    def __init__(self, state_dim, hidden_units=(64, 64), gate=F.relu):
        super(FCBody, self).__init__()
        dims = (state_dim, ) + hidden_units
        self.layers = nn.ModuleList([layer_init(nn.Linear(dim_in, dim_out)) for dim_in, dim_out in zip(dims[:-1], dims[1:])])
        self.gate = gate
        self.feature_dim = dims[-1]

    def forward(self, x):
        for idx, layer in enumerate(self.layers):
            x = layer(x)
            if idx != len(self.layers)-1:
                x = self.gate(x)
        return x

    def get_feature_shape(self):
        return self.feature_dim


class ConvolutionalBody(nn.Module):
    def __init__(self, input_shape, feature_dim=256, channels=[3, 3], kernel_sizes=[1], strides=[1], paddings=[0], dropout=0.0, non_linearities=[nn.LeakyReLU]):
        '''
        Default input channels assume a RGB image (3 channels).

        :param input_shape: dimensions of the input.
        :param feature_dim: integer size of the output.
        :param channels: list of number of channels for each convolutional layer,
                with the initial value being the number of channels of the input.
        :param kernel_sizes: list of kernel sizes for each convolutional layer.
        :param strides: list of strides for each convolutional layer.
        :param paddings: list of paddings for each convolutional layer.
        :param dropout: dropout probability to use.
        :param non_linearities: list of non-linear nn.Functional functions to use
                after each convolutional layer.
        '''
        super(ConvolutionalBody, self).__init__()
        self.dropout = dropout
        self.non_linearities = non_linearities
        if not isinstance(non_linearities, list):
            self.non_linearities = [non_linearities] * (len(channels) - 1)
        else:
            while len(self.non_linearities) <= (len(channels) - 1):
                self.non_linearities.append(self.non_linearities[0])

        self.feature_dim = feature_dim
        if isinstance(feature_dim, tuple):
            self.feature_dim = feature_dim[-1]

        self.features = []
        dim = input_shape[1] # height
        in_ch = channels[0]
        for idx, (cfg, k, s, p) in enumerate(zip(channels[1:], kernel_sizes, strides, paddings)):
            if cfg == 'M':
                layer = nn.MaxPool2d(kernel_size=k, stride=s)
                self.features.append(layer)
                # Update of the shape of the input-image, following Conv:
                dim = (dim-k)//s+1
                print(f"Dim: {dim}")
            else:
                layer = nn.Conv2d(in_channels=in_ch, out_channels=cfg, kernel_size=k, stride=s, padding=p) 
                layer = layer_init(layer, w_scale=math.sqrt(2))
                in_ch = cfg
                self.features.append(layer)
                self.features.append(self.non_linearities[idx](inplace=True))
                # Update of the shape of the input-image, following Conv:
                dim = (dim-k+2*p)//s+1
                print(f"Dim: {dim}")
        self.features = nn.Sequential(*self.features)

        self.feat_map_dim = dim 
        self.feat_map_depth = channels[-1]

        hidden_units = (dim * dim * channels[-1],)
        if isinstance(feature_dim, tuple):
            hidden_units = hidden_units + feature_dim
        else:
            hidden_units = hidden_units + (self.feature_dim,)

        self.fcs = nn.ModuleList()
        for nbr_in, nbr_out in zip(hidden_units, hidden_units[1:]):
            self.fcs.append( layer_init(nn.Linear(nbr_in, nbr_out), w_scale=math.sqrt(2)))
            if self.dropout:
                self.fcs.append( nn.Dropout(p=self.dropout))

    def _compute_feat_map(self, x):
        return self.features(x)

    def forward(self, x, non_lin_output=True):
        feat_map = self._compute_feat_map(x)

        features = feat_map.view(feat_map.size(0), -1)
        for idx, fc in enumerate(self.fcs):
            features = fc(features)
            if idx != len(self.fcs)-1 or non_lin_output:
                features = F.relu(features)

        return features

    def get_input_shape(self):
        return self.input_shape

    def get_feature_shape(self):
        return self.feature_dim


class ConvolutionalLstmBody(ConvolutionalBody):
    def __init__(self, input_shape, feature_dim=256, channels=[3, 3], kernel_sizes=[1], strides=[1], paddings=[0], dropout=0.0, non_linearities=[nn.ReLU], hidden_units=(256,), gate=F.relu):
        '''
        Default input channels assume a RGB image (3 channels).

        :param input_shape: dimensions of the input.
        :param feature_dim: integer size of the output.
        :param channels: list of number of channels for each convolutional layer,
                with the initial value being the number of channels of the input.
        :param kernel_sizes: list of kernel sizes for each convolutional layer.
        :param strides: list of strides for each convolutional layer.
        :param paddings: list of paddings for each convolutional layer.
        :param dropout: dropout probability to use.
        :param non_linearities: list of non-linear nn.Functional functions to use
                after each convolutional layer.
        '''
        super(ConvolutionalLstmBody, self).__init__(input_shape=input_shape,
                                                feature_dim=feature_dim,
                                                channels=channels,
                                                kernel_sizes=kernel_sizes,
                                                strides=strides,
                                                paddings=paddings,
                                                dropout=dropout,
                                                non_linearities=non_linearities)

        self.lstm_body = LSTMBody( state_dim=self.feature_dim, hidden_units=hidden_units, gate=gate)

    def forward(self, inputs):
        '''
        :param inputs: input to LSTM cells. Structured as (feed_forward_input, {hidden: hidden_states, cell: cell_states}).
        hidden_states: list of hidden_state(s) one for each self.layers.
        cell_states: list of hidden_state(s) one for each self.layers.
        '''
        x, recurrent_neurons = inputs
        features = super(ConvolutionalLstmBody,self).forward(x)
        return self.lstm_body( (features, recurrent_neurons))

    def get_reset_states(self, cuda=False, repeat=1):
        return self.lstm_body.get_reset_states(cuda=cuda, repeat=repeat)
    
    def get_input_shape(self):
        return self.input_shape

    def get_feature_shape(self):
        return self.lstm_body.get_feature_shape()


class ConvolutionalGruBody(ConvolutionalBody):
    def __init__(self, input_shape, feature_dim=256, channels=[3, 3], kernel_sizes=[1], strides=[1], paddings=[0], dropout=0.0, non_linearities=[nn.ReLU], hidden_units=(256,), gate=F.relu):
        '''
        Default input channels assume a RGB image (3 channels).

        :param input_shape: dimensions of the input.
        :param feature_dim: integer size of the output.
        :param channels: list of number of channels for each convolutional layer,
                with the initial value being the number of channels of the input.
        :param kernel_sizes: list of kernel sizes for each convolutional layer.
        :param strides: list of strides for each convolutional layer.
        :param paddings: list of paddings for each convolutional layer.
        :param dropout: dropout probability to use.
        :param non_linearities: list of non-linear nn.Functional functions to use
                after each convolutional layer.
        '''
        super(ConvolutionalGruBody, self).__init__(input_shape=input_shape,
                                                feature_dim=feature_dim,
                                                channels=channels,
                                                kernel_sizes=kernel_sizes,
                                                strides=strides,
                                                paddings=paddings,
                                                dropout=dropout,
                                                non_linearities=non_linearities)

        self.gru_body = GRUBody( state_dim=self.feature_dim, hidden_units=hidden_units, gate=gate)

    def forward(self, inputs):
        '''
        :param inputs: input to GRU cells. Structured as (feed_forward_input, {hidden: hidden_states, cell: cell_states}).
        hidden_states: list of hidden_state(s) one for each self.layers.
        cell_states: list of hidden_state(s) one for each self.layers.
        '''
        x, recurrent_neurons = inputs
        features = super(ConvolutionalGruBody,self).forward(x)
        return self.gru_body( (features, recurrent_neurons))

    def get_reset_states(self, cuda=False, repeat=1):
        return self.gru_body.get_reset_states(cuda=cuda, repeat=repeat)

    def get_input_shape(self):
        return self.input_shape

    def get_feature_shape(self):
        return self.gru_body.get_feature_shape()


class LSTMBody(nn.Module):
    def __init__(self, state_dim, hidden_units=(256), gate=F.relu):
        super(LSTMBody, self).__init__()
        dims = (state_dim, ) + hidden_units
        # Consider future cases where we may not want to initialize the LSTMCell(s)
        self.layers = nn.ModuleList([layer_init(nn.LSTMCell(dim_in, dim_out)) for dim_in, dim_out in zip(dims[:-1], dims[1:])])
        self.feature_dim = dims[-1]
        self.gate = gate

    def forward(self, inputs):
        '''
        :param inputs: input to LSTM cells. Structured as (feed_forward_input, {hidden: hidden_states, cell: cell_states}).
        hidden_states: list of hidden_state(s) one for each self.layers.
        cell_states: list of hidden_state(s) one for each self.layers.
        '''
        x, recurrent_neurons = inputs
        hidden_states, cell_states = recurrent_neurons['hidden'], recurrent_neurons['cell']

        next_hstates, next_cstates = [], []
        for idx, (layer, hx, cx) in enumerate(zip(self.layers, hidden_states, cell_states) ):
            batch_size = x.size(0)
            if hx.size(0) == 1: # then we have just resetted the values, we need to expand those:
                hx = torch.cat([hx]*batch_size, dim=0)
                cx = torch.cat([cx]*batch_size, dim=0)
            elif hx.size(0) != batch_size:
                raise NotImplementedError("Sizes of the hidden states and the inputs do not coincide.")

            nhx, ncx = layer(x, (hx, cx))
            next_hstates.append(nhx)
            next_cstates.append(ncx)
            # Consider not applying activation functions on last layer's output
            if self.gate is not None:
                x = self.gate(nhx)

        return x, {'hidden': next_hstates, 'cell': next_cstates}

    def get_reset_states(self, cuda=False, repeat=1):
        hidden_states, cell_states = [], []
        for layer in self.layers:
            h = torch.zeros(repeat, layer.hidden_size)
            if cuda:
                h = h.cuda()
            hidden_states.append(h)
            cell_states.append(h)
        return {'hidden': hidden_states, 'cell': cell_states}

    def get_feature_shape(self):
        return self.feature_dim


class GRUBody(nn.Module):
    def __init__(self, state_dim, hidden_units=(256), gate=F.relu):
        super(GRUBody, self).__init__()
        dims = (state_dim, ) + hidden_units
        # Consider future cases where we may not want to initialize the LSTMCell(s)
        self.layers = nn.ModuleList([layer_init(nn.GRUCell(dim_in, dim_out)) for dim_in, dim_out in zip(dims[:-1], dims[1:])])
        self.feature_dim = dims[-1]
        self.gate = gate

    def forward(self, inputs):
        '''
        :param inputs: input to LSTM cells. Structured as (feed_forward_input, {hidden: hidden_states, cell: cell_states}).
        hidden_states: list of hidden_state(s) one for each self.layers.
        cell_states: list of hidden_state(s) one for each self.layers.
        '''
        x, recurrent_neurons = inputs
        hidden_states, cell_states = recurrent_neurons['hidden'], recurrent_neurons['cell']

        next_hstates, next_cstates = [], []
        for idx, (layer, hx, cx) in enumerate(zip(self.layers, hidden_states, cell_states) ):
            batch_size = x.size(0)
            if hx.size(0) == 1: # then we have just resetted the values, we need to expand those:
                hx = torch.cat([hx]*batch_size, dim=0)
                cx = torch.cat([cx]*batch_size, dim=0)
            elif hx.size(0) != batch_size:
                raise NotImplementedError("Sizes of the hidden states and the inputs do not coincide.")

            nhx = layer(x, hx)
            next_hstates.append(nhx)
            next_cstates.append(nhx)
            # Consider not applying activation functions on last layer's output
            if self.gate is not None:
                x = self.gate(nhx)

        return x, {'hidden': next_hstates, 'cell': next_cstates}

    def get_reset_states(self, cuda=False, repeat=1):
        hidden_states, cell_states = [], []
        for layer in self.layers:
            h = torch.zeros(repeat, layer.hidden_size)
            if cuda:
                h = h.cuda()
            hidden_states.append(h)
            cell_states.append(h)
        return {'hidden': hidden_states, 'cell': cell_states}

    def get_feature_shape(self):
        return self.feature_dim

class ModelResNet18(models.ResNet):
    def __init__(self, input_shape, feature_dim=256, nbr_layer=None, pretrained=False):
        '''
        Default input channels assume a RGB image (3 channels).

        :param input_shape: dimensions of the input.
        :param feature_dim: integer size of the output.
        :param nbr_layer: int, number of convolutional residual layer to use.
        :param pretrained: bool, specifies whether to load a pretrained model.
        '''
        super(ModelResNet18, self).__init__(BasicBlock, [2, 2, 2, 2])
        if pretrained:
            self.load_state_dict(model_zoo.load_url(model_urls['resnet18']))
        
        self.input_shape = input_shape
        self.nbr_layer = nbr_layer
        
        # Re-organize the input conv layer:
        saved_kernel = self.conv1.weight.data
        
        if input_shape[0] >3:
            '''
            in3depth = input_shape[0] // 3
            concat_kernel = []
            for i in range(in3depth):
                concat_kernel.append( saved_kernel)
            concat_kernel = torch.cat(concat_kernel, dim=1)

            self.conv1 = nn.Conv2d(in3depth*3, 64, kernel_size=7, stride=2, padding=3, bias=False)
            self.conv1.weight.data = concat_kernel
            '''
            self.conv1 = nn.Conv2d(input_shape[0], 64, kernel_size=7, stride=2, padding=3, bias=False)
            self.conv1.weight.data[:,0:3,...] = saved_kernel
            
        elif input_shape[0] <3:
            self.conv1 = nn.Conv2d(input_shape[0], 64, kernel_size=7, stride=2, padding=3, bias=False)
            self.conv1.weight.data = saved_kernel[:,0:input_shape[0],...]

        # 64:
        self.avgpool_ksize = 2
        # 224:
        #self.avgpool_ksize = 7
        self.avgpool = nn.AvgPool2d(self.avgpool_ksize, stride=1)
        
        # Add the fully-connected layers at the top:
        self.feature_dim = feature_dim
        if isinstance(feature_dim, tuple):
            self.feature_dim = feature_dim[-1]

        # Compute the number of features:
        self.feat_map_dim, self.feat_map_depth = self._compute_feature_shape(input_shape[-1], self.nbr_layer)
        # Avg Pool:
        feat_dim = self.feat_map_dim-1
        num_ftrs = self.feat_map_depth * feat_dim * feat_dim
        
        self.fc = layer_init(nn.Linear(num_ftrs, self.feature_dim), w_scale=math.sqrt(2))
    
    def _compute_feature_shape(self, input_dim, nbr_layer):
        if nbr_layer is None: return self.fc.in_features

        layers_depths = [64,128,256,512]
        layers_divisions = [1,2,2,2]

        # Conv1:
        dim = input_dim // 2
        # MaxPool1:
        dim = dim // 2

        depth = 64
        for idx_layer in range(nbr_layer):
            dim = math.ceil(float(dim) / layers_divisions[idx_layer])
            depth = layers_depths[idx_layer]
            print(dim, depth)

        return dim, depth

    def _compute_feat_map(self, x):
        #xsize = x.size()
        #print('input:',xsize)
        x = self.conv1(x)
        #xsize = x.size()
        #print('cv0:',xsize)
        x = self.bn1(x)
        x = self.relu(x)
        
        self.x0 = self.maxpool(x)
        
        #xsize = self.x0.size()
        #print('mxp0:',xsize)

        if self.nbr_layer >= 1:
            self.x1 = self.layer1(self.x0)
            #xsize = self.x1.size()
            #print('1:',xsize)
            if self.nbr_layer >= 2:
                self.x2 = self.layer2(self.x1)
                #xsize = self.x2.size()
                #print('2:',xsize)
                if self.nbr_layer >= 3:
                    self.x3 = self.layer3(self.x2)
                    #xsize = self.x3.size()
                    #print('3:',xsize)
                    if self.nbr_layer >= 4:
                        self.x4 = self.layer4(self.x3)
                        #xsize = self.x4.size()
                        #print('4:',xsize)
                        
                        self.features_map = self.x4
                    else:
                        self.features_map = self.x3
                else:
                    self.features_map = self.x2
            else:
                self.features_map = self.x1
        else:
            self.features_map = self.x0
        
        return self.features_map

    def _compute_features(self, features_map):
        avgx = self.avgpool(features_map)
        #xsize = avgx.size()
        #print('avg: x:',xsize)
        fcx = avgx.view(avgx.size(0), -1)
        #xsize = fcx.size()
        #print('reg avg: x:',xsize)
        fcx = self.fc(fcx)
        #xsize = fcx.size()
        #print('fc output: x:',xsize)
        return fcx

    def forward(self, x):
        self.features_map = self._compute_feat_map(x)
        self.features = self._compute_features(self.features_map)
        return self.features

    def get_feature_shape(self):
        return self.feature_dim


class MHDPA(nn.Module):
    def __init__(self,depth_dim=24+11+2,
                    interactions_dim=64, 
                    hidden_size=256):
        super(MHDPA,self).__init__()

        self.depth_dim = depth_dim
        self.interactions_dim = interactions_dim
        self.hidden_size = hidden_size
        self.fXY = None 
        self.batch = None 
        
        self.queryGenerator = nn.Linear(self.depth_dim,self.interactions_dim,bias=False)
        self.keyGenerator = nn.Linear(self.depth_dim,self.interactions_dim,bias=False)
        self.valueGenerator = nn.Linear(self.depth_dim,self.interactions_dim,bias=False)
            
        self.queryGenerator_layerNorm = nn.LayerNorm(self.interactions_dim,elementwise_affine=False)
        self.keyGenerator_layerNorm = nn.LayerNorm(self.interactions_dim,elementwise_affine=False)
        self.valueGenerator_layerNorm = nn.LayerNorm(self.interactions_dim,elementwise_affine=False)
        
    def addXYfeatures(self,x,outputFsizes=False):
        xsize = x.size()
        batch = xsize[0]
        if self.batch != batch or self.fXY is None:
            # batch x depth x X x Y
            self.batch = xsize[0]
            self.depth = xsize[1]
            self.sizeX = xsize[2]
            self.sizeY = xsize[3]
            stepX = 2.0/self.sizeX
            stepY = 2.0/self.sizeY

            fx = torch.zeros((self.batch,1,self.sizeX,1))
            fy = torch.zeros((self.batch,1,1,self.sizeY))
            vx = -1+0.5*stepX
            for i in range(self.sizeX):
                fx[:,:,i,:] = vx
                vx += stepX
            vy = -1+0.5*stepY
            for i in range(self.sizeY):
                fy[:,:,:,i] = vy
                vy += stepY
            fxy = fx.repeat( 1,1,1,self.sizeY)
            fyx = fy.repeat( 1,1,self.sizeX,1)
            fXY = torch.cat( [fxy,fyx], dim=1)
            self.fXY = fXY 

        self.fXY = self.fXY.to(x.device)
        out = torch.cat( [x,self.fXY], dim=1)
        out = out.view((self.batch,self.depth+2,-1))

        if outputFsizes:
            return out, self.sizeX, self.sizeY

        return out 

    def forward(self,x, usef=False):
        # input: b x d x f
        batchsize = x.size()[0]
        depth_dim = x.size()[1]
        featuresize = x.size()[2]
        updated_entities = []
        
        xb = x.transpose(1,2).contiguous()
        # batch x depth_dim x featuremap_dim^2: stack of column entity: d x f   
        #  b x f x d   

        augx_full_flat = xb.view( batchsize*featuresize, -1) 
        # ( batch*featuresize x depth )
        query = self.queryGenerator( augx_full_flat )
        key = self.keyGenerator( augx_full_flat )
        value = self.valueGenerator( augx_full_flat )
        # b*f x i
        
        query = self.queryGenerator_layerNorm(query)
        key = self.keyGenerator_layerNorm(key)
        value = self.valueGenerator_layerNorm(value)
        # b*f x interactions_dim

        query = query.view((batchsize, featuresize, self.interactions_dim))
        key = key.view((batchsize, featuresize, self.interactions_dim))
        value = value.view((batchsize, featuresize, self.interactions_dim))
        # b x f x interactions_dim
        
        att = torch.matmul(query, key.transpose(-2,-1) ) / math.sqrt(self.interactions_dim)
        weights = F.softmax( att, dim=1 )
        # b x f x i * b x i x f --> b x f x f
        sdpa_out = torch.matmul( weights, value)
        # b x f x f * b x f x i = b x f x i 
        return sdpa_out 
    
    def save(self,path):
        wts = self.state_dict()
        rnpath = path + 'MHDPA.weights'
        torch.save( wts, rnpath )
        print('MHDPA saved at: {}'.format(rnpath) )


    def load(self,path):
        rnpath = path + 'MHDPA.weights'
        self.load_state_dict( torch.load( rnpath ) )
        print('MHDPA loaded from: {}'.format(rnpath) )



class MHDPA_RN(nn.Module):
    def __init__(self,
                 depth_dim=24+11+2, 
                 nbrHead=3,
                 nbrRecurrentSharedLayers=1,
                 nbrEntity=7,
                 units_per_MLP_layer=256,
                 interactions_dim=128,
                 output_dim=None,
                 dropout_prob=0.0):
        super(MHDPA_RN,self).__init__()

        self.nbrEntity = nbrEntity
        self.output_dim = output_dim
        self.depth_dim = depth_dim
        self.dropout_prob = dropout_prob

        self.nbrHead = nbrHead
        self.nbrRecurrentSharedLayers = nbrRecurrentSharedLayers
        
        self.units_per_MLP_layer = units_per_MLP_layer 
        self.interactions_dim = interactions_dim 
        self.use_bias = False 

        self.MHDPAs = nn.ModuleList()
        for i in range(self.nbrHead):
            self.MHDPAs.append(MHDPA(depth_dim=self.depth_dim,interactions_dim=self.interactions_dim))

        self.nonLinearModule = nn.LeakyReLU
        
        # Layer Normalization at the spatial level:
        self.layerNorm = nn.LayerNorm(self.nbrEntity )
        # F function:
        self.f = nn.Sequential( nn.Linear(self.nbrHead*self.interactions_dim,self.units_per_MLP_layer,bias=self.use_bias),
                                        self.nonLinearModule(),
                                        nn.Linear(self.units_per_MLP_layer,self.units_per_MLP_layer,bias=self.use_bias),
                                        self.nonLinearModule(),
                                        nn.Linear(self.units_per_MLP_layer,self.depth_dim,bias=self.use_bias)                                              
                                                )
        # FF final layer: MLP2
        # computes a representation over the spatially-max-pooled or flattened representation:
        if self.output_dim is not None:
            self.fout_input_dim = int( (self.depth_dim) * self.nbrEntity )
            self.fout = nn.Sequential( nn.Linear(self.fout_input_dim,self.units_per_MLP_layer,bias=self.use_bias),
                                            self.nonLinearModule(),
                                            nn.Linear(self.units_per_MLP_layer,self.output_dim,bias=self.use_bias))

    def forwardScaledDPAhead(self, x, head, reset_hidden_states=False):
        # input: b x d x f
        output = self.MHDPAs[head](x,usef=False)
        # batch x f x i or batch x output_dim
        return output 

    def forwardStackedMHDPA(self, augx):
        # input: b x d x f
        MHDPAouts = []
        for i in range(self.nbrHead):
            MHDPAouts.append( self.forwardScaledDPAhead(augx,head=i) )
            # head x [ batch x f x i ]
        concatOverHeads = torch.cat( MHDPAouts, dim=2)
        # (batch x f x nbr_head*interaction_dim)
        
        input4f = concatOverHeads.view((self.batchsize*self.featuresize, -1))
        # (batch*f x nbr_head*interaction_dim)
        
        updated_entities = self.f(input4f).view((self.batchsize, self.featuresize, self.depth_dim))
        # (batch x f x depth_dim)
        
        updated_entities = self.layerNorm( updated_entities.transpose(1,2))
        # (batch x depth_dim x f )
        updated_entities = F.dropout2d(updated_entities, p=self.dropout_prob)
        
        res_updated_entities = augx + updated_entities
        # (batch x depth_dim x f )
        return res_updated_entities

    def forward(self, x=None, augx=None):
        if x is None:
            if augx is not None:
                x = augx 
            else:
                raise NotImplementedError
        self.batchsize = x.size()[0]
        
        augxNone = True
        if augx is None:
            # add coordinate channels:
            augx, self.sizeX, self.sizeY = self.MHDPAs[0].addXYfeatures(x,outputFsizes=True)
            self.featuresize = self.sizeX*self.sizeY
            # batch x d x f(=featuremap_dim^2)
        else:
            augxNone = False
            self.featuresize = augx.size(-1)

        # Compute MHDPA towards convergence...
        self.outputRec = [augx]
        for i in range(self.nbrRecurrentSharedLayers):
            # input/output: b x d x f
            self.outputRec.append(self.forwardStackedMHDPA(self.outputRec[i]))
        
        # Retrieve the (hopefully) converged representation:    
        intermediateOutput = self.outputRec[-1]
        if augxNone:
            intermediateOutput = intermediateOutput.view( (self.batchsize, self.depth_dim, self.sizeX,self.sizeY))
            # batch x d x sizeX x sizeX=sizeY

        if self.output_dim is not None:
            # Flattening:
            intermediateOutput = intermediateOutput.view( (self.batchsize, -1) )    
            # batch x d*sizeX*sizeY

            foutput = self.fout(intermediateOutput)
            # batch x d*sizeX*sizeY/ d --> batch x output_dim
            return foutput

        return intermediateOutput


class ConvolutionalMHDPABody(ConvolutionalBody):
    def __init__(self, 
                 input_shape, 
                 feature_dim=256, 
                 channels=[3, 3], 
                 kernel_sizes=[1], 
                 strides=[1], 
                 paddings=[0], 
                 dropout=0.0, 
                 non_linearities=[nn.LeakyReLU],
                 nbrHead=4,
                 nbrRecurrentSharedLayers=1,  
                 units_per_MLP_layer=512,
                 interaction_dim=128):
        '''
        Default input channels assume a RGB image (3 channels).

        :param input_shape: dimensions of the input.
        :param feature_dim: integer size of the output.
        :param channels: list of number of channels for each convolutional layer,
                         with the initial value being the number of channels of the input.
        :param kernel_sizes: list of kernel sizes for each convolutional layer.
        :param strides: list of strides for each convolutional layer.
        :param paddings: list of paddings for each convolutional layer.
        :param dropout: dropout probability to use.
        :param non_linearities: list of non-linear nn.Functional functions to use
                                after each convolutional layer.
        :param nbrHead: Int, number of Scaled Dot-Product Attention head.
        :param nbrRecurrentSharedLayers: Int, number of recurrent update to apply.
        :param units_per_MLP_layer: Int, number of neurons in the transformation from the
                                    concatenated head outputs to the entity embedding space.
        :param interaction_dim: Int, number of dimensions in the interaction space.
        '''
        super(ConvolutionalMHDPABody, self).__init__(input_shape=input_shape,
                                                     feature_dim=feature_dim,
                                                     channels=channels,
                                                     kernel_sizes=kernel_sizes,
                                                     strides=strides,
                                                     paddings=paddings,
                                                     dropout=dropout,
                                                     non_linearities=non_linearities)       
        
        self.relationModule = MHDPA_RN(output_dim=None,
                                       depth_dim=channels[-1]+2,
                                       nbrHead=nbrHead, 
                                       nbrRecurrentSharedLayers=nbrRecurrentSharedLayers, 
                                       nbrEntity=self.feat_map_dim*self.feat_map_dim,  
                                       units_per_MLP_layer=units_per_MLP_layer,
                                       interactions_dim=interaction_dim,
                                       dropout_prob=dropout)
        
        hidden_units = (self.feat_map_dim * self.feat_map_dim * (channels[-1]+2),)
        if isinstance(feature_dim, tuple):
            hidden_units = hidden_units + feature_dim
        else:
            hidden_units = hidden_units + (self.feature_dim,)

        self.fcs = nn.ModuleList()
        for nbr_in, nbr_out in zip(hidden_units, hidden_units[1:]):
            self.fcs.append( layer_init(nn.Linear(nbr_in, nbr_out), w_scale=math.sqrt(2)))#1e-2))#1.0/math.sqrt(nbr_in*nbr_out)))
            if self.dropout:
                self.fcs.append( nn.Dropout(p=self.dropout))

    def forward(self, x):
        x = self._compute_feat_map(x) 

        xsize = x.size()
        batchsize = xsize[0]
        depthsize = xsize[1]
        spatialSize = xsize[2]
        featuresize = spatialSize*spatialSize

        feat_map = self.relationModule(x)

        features = feat_map.view(feat_map.size(0), -1)
        for idx, fc in enumerate(self.fcs):
            features = fc(features)
            features = F.leaky_relu(features)

        return features


class ResNet18MHDPA(ModelResNet18):
    def __init__(self, 
                 input_shape, 
                 feature_dim=256, 
                 nbr_layer=None, 
                 pretrained=False, 
                 dropout=0.0, 
                 non_linearities=[nn.LeakyReLU],
                 nbrHead=4,
                 nbrRecurrentSharedLayers=1,  
                 units_per_MLP_layer=512,
                 interaction_dim=128):
        '''
        Default input channels assume a RGB image (3 channels).

        :param input_shape: dimensions of the input.
        :param feature_dim: integer size of the output.
        :param nbr_layer: int, number of convolutional residual layer to use.
        :param pretrained: bool, specifies whether to load a pretrained model.
        :param dropout: dropout probability to use.
        :param non_linearities: list of non-linear nn.Functional functions to use
                                after each convolutional layer.
        :param nbrHead: Int, number of Scaled Dot-Product Attention head.
        :param nbrRecurrentSharedLayers: Int, number of recurrent update to apply.
        :param units_per_MLP_layer: Int, number of neurons in the transformation from the
                                    concatenated head outputs to the entity embedding space.
        :param interaction_dim: Int, number of dimensions in the interaction space.
        '''
        super(ResNet18MHDPA, self).__init__(input_shape=input_shape,
                                            feature_dim=feature_dim,
                                            nbr_layer=nbr_layer,
                                            pretrained=pretrained)       
        self.dropout = dropout
        self.relationModule = MHDPA_RN(output_dim=None,
                                       depth_dim=self.feat_map_depth+2,
                                       nbrHead=nbrHead, 
                                       nbrRecurrentSharedLayers=nbrRecurrentSharedLayers, 
                                       nbrEntity=self.feat_map_dim*self.feat_map_dim,  
                                       units_per_MLP_layer=units_per_MLP_layer,
                                       interactions_dim=interaction_dim,
                                       dropout_prob=self.dropout)
        
        hidden_units = (self.feat_map_dim * self.feat_map_dim * (self.feat_map_depth+2),)
        if isinstance(feature_dim, tuple):
            hidden_units = hidden_units + feature_dim
        else:
            hidden_units = hidden_units + (self.feature_dim,)

        self.fcs = nn.ModuleList()
        for nbr_in, nbr_out in zip(hidden_units, hidden_units[1:]):
            self.fcs.append( layer_init(nn.Linear(nbr_in, nbr_out), w_scale=math.sqrt(2)))#1e-2))#1.0/math.sqrt(nbr_in*nbr_out)))
            if self.dropout:
                self.fcs.append( nn.Dropout(p=self.dropout))

    def forward(self, x):
        x = self._compute_feat_map(x) 

        xsize = x.size()
        batchsize = xsize[0]
        depthsize = xsize[1]
        spatialSize = xsize[2]
        featuresize = spatialSize*spatialSize

        feat_map = self.relationModule(x)

        features = feat_map.view(feat_map.size(0), -1)
        for idx, fc in enumerate(self.fcs):
            features = fc(features)
            features = F.leaky_relu(features)

        return features


import torchvision.models.vgg as vgg_module


class VGG(nn.Module):
    '''
    Making the VGG architecture usable as a classification-layer-free
    convolutional architecture to choose from.
    '''
    def __init__(self, features, num_classes=1000, init_weights=True):
        super(VGG, self).__init__()
        self.features = features
        self.avgpool = nn.AdaptiveAvgPool2d((7, 7))
        # Not really a classifier anymore:
        self.classifier = nn.Sequential(
            nn.Linear(512 * 7 * 7, 4096),
            nn.ReLU(True),
            nn.Dropout(),
            nn.Linear(4096, 4096),
            nn.ReLU(True),
            nn.Dropout(),
        #    nn.Linear(4096, num_classes),
        )
        if init_weights:
            self._initialize_weights()

    def forward(self, x):
        x = self.features(x)
        x = self.avgpool(x)
        x = torch.flatten(x, 1)
        x = self.classifier(x)
        return x

    def _initialize_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, 0, 0.01)
                nn.init.constant_(m.bias, 0)


from torchvision.models.vgg import make_layers, cfgs, load_state_dict_from_url 
from torchvision.models.vgg import model_urls as vgg_model_urls


def _vgg(arch, cfg, batch_norm, pretrained, progress, **kwargs):
    if pretrained:
        kwargs['init_weights'] = False
    model = VGG(make_layers(cfgs[cfg], batch_norm=batch_norm), **kwargs)
    if pretrained:
        state_dict = load_state_dict_from_url(vgg_model_urls[arch],
                                              progress=progress)
        model.load_state_dict(state_dict, strict=False)
    return model


vgg_module.VGG = VGG
vgg_module._vgg = _vgg

class ModelVGG16(nn.Module):
    def __init__(self, input_shape, feature_dim=512, pretrained=True, final_layer_idx=None):
        super(ModelVGG16, self).__init__()
        self.input_shape = input_shape
        self.feature_dim = feature_dim
        self.final_layer_idx = final_layer_idx
        self.features = torchvision.models.vgg.vgg16(pretrained=pretrained).features

        # Re-organize the input conv layer:
        if self.input_shape[0]>3:
            saved_weights = getattr(self.features[0], "weight", None)
            saved_bias = getattr(self.features[0], "bias", None)
            self.features[0] = nn.Conv2d(self.input_shape[0], 64, kernel_size=3, padding=1)
            if saved_weights is not None:   self.features[0].weight.data[:,0:3,...] = saved_weights.data
            if saved_bias is not None:   self.features[0].bias.data = saved_bias.data
        elif self.input_shape[0]<3:
            saved_weights = getattr(self.features[0], "weight", None)
            saved_bias = getattr(self.features[0], "bias", None)
            self.features[0] = nn.Conv2d(self.input_shape[0], 64, kernel_size=3, padding=1)
            if saved_weights is not None:   self.features[0].weight.data = saved_weights.data[:,0:self.input_shape[0],...]
            if saved_bias is not None:  self.features[0].bias.data = saved_bias.data
        
        if self.final_layer_idx is not None:
            assert(isinstance(self.final_layer_idx, int) and self.final_layer_idx>0)
            while (len(self.features)-self.final_layer_idx)>0:
                del self.features[-1]

        # Output layer:
        feature_shape = retrieve_output_shape(input=torch.zeros(input_shape), model=self.features)
        self.fc = nn.Linear(feature_shape[0]*feature_shape[1]*feature_shape[0], self.feature_dim)
    
    def forward(self, x):
        x = self.features(x)
        x = self.fc(x)
        return x

    def get_feature_shape(self):
        return self.feature_dim


class ExtractorVGG16(nn.Module):
    def __init__(self, input_shape, final_layer_idx=None, pretrained=True):
        super(ExtractorVGG16, self).__init__()
        self.input_shape = input_shape
        self.final_layer_idx = final_layer_idx
        self.features = torchvision.models.vgg.vgg16(pretrained=pretrained).features
        
        # Re-organize the input conv layer:
        if self.input_shape[0]>3:
            saved_weights = getattr(self.features[0], "weight", None)
            saved_bias = getattr(self.features[0], "bias", None)
            self.features[0] = nn.Conv2d(self.input_shape[0], 64, kernel_size=3, padding=1)
            if saved_weights is not None:   self.features[0].weight.data[:,0:3,...] = saved_weights.data
            if saved_bias is not None:   self.features[0].bias.data = saved_bias.data
        elif self.input_shape[0]<3:
            saved_weights = getattr(self.features[0], "weight", None)
            saved_bias = getattr(self.features[0], "bias", None)
            self.features[0] = nn.Conv2d(self.input_shape[0], 64, kernel_size=3, padding=1)
            if saved_weights is not None:   self.features[0].weight.data = saved_weights.data[:,0:self.input_shape[0],...]
            if saved_bias is not None:  self.features[0].bias.data = saved_bias.data
        
        if self.final_layer_idx is not None:
            assert(isinstance(self.final_layer_idx, int) and self.final_layer_idx>0)
            while (len(self.features)-self.final_layer_idx)>0:
                del self.features[-1]

    def forward(self, x):
        return self.features(x)


class ExtractorResNet18(ModelResNet18):
    def __init__(self, input_shape, final_layer_idx=None, pretrained=True):
        super(ExtractorResNet18, self).__init__(input_shape=input_shape, feature_dim=1, nbr_layer=final_layer_idx, pretrained=pretrained)
        self.input_shape = input_shape
        self.final_layer_idx = final_layer_idx
        
    def forward(self, x):
        return self._compute_feat_map(x)
        