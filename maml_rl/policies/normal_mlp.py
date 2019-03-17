import math
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions import Normal

from collections import OrderedDict
from maml_rl.policies.policy import Policy, weight_init

class NormalMLPPolicy(Policy):
    """Policy network based on a multi-layer perceptron (MLP), with a 
    `Normal` distribution output, with trainable standard deviation. This 
    policy network can be used on tasks with continuous action spaces (eg. 
    `HalfCheetahDir`). The code is adapted from 
    https://github.com/cbfinn/maml_rl/blob/9c8e2ebd741cb0c7b8bf2d040c4caeeb8e06cc95/sandbox/rocky/tf/policies/maml_minimal_gauss_mlp_policy.py
    """
    def __init__(self, input_size, output_size, hidden_sizes=(),
                 nonlinearity=F.relu, init_std=1.0, min_std=1e-6):
        super(NormalMLPPolicy, self).__init__(
            input_size=input_size, output_size=output_size)


        
        # ---------------   REMEMBER to make changes if needed ------------------------
        self.ped_num = 4
        self.robot_state_before_rotate = 6
        self.ped_state_before_rotate = 4
        # ---------------   Finish making changes ------------------------
        
        # Social Attention (w/o local map) Begin
        self.robot_state_dim = 3 # This is After rotate   # Remember to replace if needed
        self.ped_state_dim = 5 # This is After rotate
        input_dim = self.robot_state_dim + self.ped_state_dim # 13 # robot_state + human_state  # Replace if needed!
        self.mlp1_dims = [150, 100]
        self.mlp2_dims = [100, 50]
        self.attention_dims = [100, 100, 1] # one score for one human
        self.mlp3_dims = [150, 100, 100, 2] # output_size is 2

        # self.mlp1 = mlp(input_dim, mlp1_dims, last_relu=True)
        mlp1_layer_sizes = [input_dim] + self.mlp1_dims
        for i in range(1, len(mlp1_layer_sizes)):
            self.add_module('mlp1_layer{0}'.format(i), nn.Linear(mlp1_layer_sizes[i - 1], mlp1_layer_sizes[i]))
   
        # self.mlp2 = mlp(mlp1_dims[-1], mlp2_dims)
        mlp2_layer_sizes = [self.mlp1_dims[-1]] + self.mlp2_dims
        for i in range(1, len(mlp2_layer_sizes)):
            self.add_module('mlp2_layer{0}'.format(i), nn.Linear(mlp2_layer_sizes[i - 1], mlp2_layer_sizes[i]))

        # self.attention = mlp(mlp1_dims[-1], attention_dims)
        attention_layer_size = [self.mlp1_dims[-1]] + self.attention_dims
        for i in range(1, len(attention_layer_size)):
            self.add_module('attention{0}'.format(i), nn.Linear(attention_layer_size[i - 1], attention_layer_size[i]))

        mlp3_input_dim = self.mlp2_dims[-1] + self.robot_state_dim
        # self.mlp3 = mlp(mlp3_input_dim, mlp3_dims)
        mlp3_layer_sizes = [mlp3_input_dim] + self.mlp3_dims
        for i in range(1, len(mlp3_layer_sizes)):
            self.add_module('mlp3_layer{0}'.format(i), nn.Linear(mlp3_layer_sizes[i - 1], mlp3_layer_sizes[i]))

        # Social Attention End
        input_size = self.mlp3_dims[-1]

        # The parameters below are not used and can be deleted
        self.hidden_sizes = hidden_sizes
        self.nonlinearity = nonlinearity
        self.min_log_std = math.log(min_std)
        self.num_layers = len(hidden_sizes) + 1
        
        # normalize the output action
        self.sigma = nn.Parameter(torch.Tensor(output_size))
        self.sigma.data.fill_(math.log(init_std))
        self.apply(weight_init)

    def forward(self, state, params=None):

        if params is None:
            params = OrderedDict(self.named_parameters())

        # Social Attention (w/o local map) Begin

                # mlp1_output = self.mlp1(state.view((-1, size[2]))) # (traj# * - * -, hidden size) = (100 * 20 * 5, 100)
        state_before_rotate = convert_to_robot_ped_pair(state.float(), self.robot_state_before_rotate, self.ped_state_before_rotate, self.ped_num)
        state, rot = rotate(state_before_rotate) # state after rotate (100, 20, 5, 9)
        robot_state = state[..., 0, :self.robot_state_dim]  

        size = state.shape

        mlp1_output = state.view((-1, size[-1])) # this is actually input

        for i in range(1, len(self.mlp1_dims)+1):
            mlp1_output = F.linear(mlp1_output, weight=params['mlp1_layer{0}.weight'.format(i)], bias=params['mlp1_layer{0}.bias'.format(i)])
            mlp1_output = self.nonlinearity(mlp1_output)

        # mlp2_output = self.mlp2(mlp1_output) # (traj# * - * -, hidden size) = (100 * 20 * 5, 50)
        mlp2_output = mlp1_output # mlp2_output here is actually input
        layers = len(self.mlp2_dims)+1
        for i in range(1, layers):
            mlp2_output = F.linear(mlp2_output, weight=params['mlp2_layer{0}.weight'.format(i)], bias=params['mlp2_layer{0}.bias'.format(i)])
            if i != layers - 1:
                mlp2_output = self.nonlinearity(mlp2_output)
           
  
        attention_output = mlp1_output # (100 * 20 * 5, 100)
        layers = len(self.attention_dims)+1
        for i in range(1, layers):
            attention_output = F.linear(attention_output, weight=params['attention{0}.weight'.format(i)], bias=params['attention{0}.bias'.format(i)])
            if i != layers - 1:
                attention_output = self.nonlinearity(attention_output)


        if len(size) == 4:
            scores = attention_output.view(size[0], size[1], size[2], 1).squeeze(dim=3) # (100, 20, 5)
        elif len(size) == 3:
            scores = attention_output.view(size[0], size[1], 1).squeeze(dim=2) # (100, 20, 5)
        elif len(size) == 2:
            scores = attention_output.view(size[0], 1).squeeze(dim=1) # (100, 20, 5)
        else:
            sys.exit('Execution stopped: Something wrong with score size '+str(scores.shape))

      
        scores_exp = torch.exp(scores) * (scores != 0).float() # (100, 20, 5)

        score_sum = torch.sum(scores_exp, dim=len(size)-2, keepdim=True) # (100, 20, 1)

    
        # comment out weights here so that NN can ignore this differentiated Tensor
        weights = (scores_exp / (score_sum + 1e-5)).unsqueeze(len(size)-1) # (100, 20, 5, 1)

        # output feature is a linear combination of input features
        if len(size) == 4:
            features = mlp2_output.view(size[0], size[1], size[2], -1)  # (100, 20, 5, 50)
        elif len(size) == 3:
            features = mlp2_output.view(size[0], size[1], -1) 
        elif len(size) == 2:
            features = mlp2_output.view(size[0], -1) 

        # weights = torch.ones(scores_exp.shape).unsqueeze(len(size)-1)
        weighted_feature = torch.sum(torch.mul(weights, features), dim=len(size)-2) # (100, 20, 50)
        # weighted_feature = torch.sum(features, dim=len(size)-2) # (100, 20, 50)


      
        # concatenate agent's state with global weighted humans' state
        joint_state = torch.cat([robot_state, weighted_feature], dim=len(size)-2) # (100, 20, 56)



        # action_value = self.mlp3(joint_state) # (100, 20, 2)
        mlp3_output = joint_state # mlp3_output here is actually input
        layers = len(self.mlp3_dims)+1
        for i in range(1, layers):
            mlp3_output = F.linear(mlp3_output, weight=params['mlp3_layer{0}.weight'.format(i)], bias=params['mlp3_layer{0}.bias'.format(i)])
            if i != layers - 1:
                mlp3_output = self.nonlinearity(mlp3_output)

        # Social Attention End
        action_value = mlp3_output
        # print("prior action_value: ", action_value.shape)

        # rotate back to original coordinates
        rot = rot[...,0]
        action_x = (action_value[..., 0] * torch.cos(-rot) + action_value[..., 1] * torch.sin(-rot)).unsqueeze(dim = len(size)-2) # (100, 20, 1)
        action_y = (action_value[..., 1] * torch.cos(-rot) - action_value[..., 0] * torch.sin(-rot)).unsqueeze(dim = len(size)-2)
        action_value = torch.cat([action_x, action_y], dim=len(size)-2)

        

        # -------- original MAML layers -------------------
        # output = action_value
        # for i in range(1, self.num_layers):
        #     output = F.linear(output, weight=params['layer{0}.weight'.format(i)], bias=params['layer{0}.bias'.format(i)])
        #     output = self.nonlinearity(output)
        # mu = F.linear(output, weight=params['mu.weight'], bias=params['mu.bias'])
        scale = torch.exp(torch.clamp(params['sigma'], min=self.min_log_std))
        # -------- FINISH MAML layers -------------------


        return Normal(loc=action_value, scale=scale)

# robot_state_dim = 2
# ped_state_dim = 2
# ped_num = 5
# state = torch.zeros((100,20, 12))  # 1 robot, 4 ped
def convert_to_robot_ped_pair(state, robot_state_dim, ped_state_dim, ped_num):
    size = state.shape
    if len(size) == 3:
        robot_state = state[:,:, :robot_state_dim].unsqueeze(dim=len(size)-1)
        ped_state = state[:,:, robot_state_dim:].view(size[0], size[1], ped_num, ped_state_dim)
    if len(size) == 2:
        robot_state = state[:,:robot_state_dim].unsqueeze(dim=len(size)-1)
        ped_state = state[:,robot_state_dim:].view(size[0], ped_num, ped_state_dim)
    if len(size) == 1:
        robot_state = state[:robot_state_dim].unsqueeze(dim=len(size)-1)
        ped_state = state[robot_state_dim:].view(ped_num, ped_state_dim)

    ped_state_list = torch.split(ped_state, 1, dim=len(size)-1)

    robot_ped_pair_list = []
    for i in range(ped_num):
        robot_ped_pair_list.append(torch.cat((robot_state, ped_state_list[i]), dim=len(size)))

    robot_ped_pairs = torch.cat(robot_ped_pair_list, dim=len(size)-1)
    return robot_ped_pairs #, robot_state.squeeze(dim =len(size)-1)


def rotate(state):
    """
    Transform the coordinate to agent-centric.
    Input state tensor is of size (batch_size, state_length)
    """
    # 'px', 'py', 'vx', 'vy', 'gx', 'gy', 'px1', 'py1', 'vx1', 'vy1'
    #  0     1      2     3      4    5     6      7      8       9     

    # print("rotate(state) input shape: ", state.shape) # [8, 4, 11]
    # print(" ")
    size = state.shape # (100, 20, 11) 
    last_dim = len(size) - 1 # 3
    dx = state[..., 4] - state[..., 0] # (100, 20)
    dy = state[..., 5] - state[..., 1]
    rot = torch.atan2(dy, dx) # (100, 20)

    dg = torch.norm(torch.cat([dx.unsqueeze(dim = last_dim), dy.unsqueeze(dim = last_dim)], dim=last_dim), 2, dim=last_dim, keepdim=True) # (100, 20, 1)

    vx = (state[..., 2] * torch.cos(rot) + state[..., 3] * torch.sin(rot)).unsqueeze(dim = last_dim) # (100, 20, 1)
    vy = (state[..., 3] * torch.cos(rot) - state[..., 2] * torch.sin(rot)).unsqueeze(dim = last_dim)

   
    vx1 = (state[..., 8] * torch.cos(rot) + state[..., 9] * torch.sin(rot)).unsqueeze(dim = last_dim) # (100, 20, 1)
    vy1 = (state[..., 9] * torch.cos(rot) - state[..., 8] * torch.sin(rot)).unsqueeze(dim = last_dim)

    px1 = (state[..., 6] - state[..., 0]) * torch.cos(rot) + (state[..., 7] - state[..., 1]) * torch.sin(rot) # (100, 20)
    px1 = px1.unsqueeze(dim = last_dim) # (100, 20, 1)

    py1 = (state[..., 7] - state[..., 1]) * torch.cos(rot) - (state[..., 6] - state[..., 0]) * torch.sin(rot)
    py1 = py1.unsqueeze(dim = last_dim)
    # radius1 = state[..., 10].unsqueeze(dim = last_dim)

    da = torch.norm(torch.cat([(state[..., 0] - state[..., 6]).unsqueeze(dim = last_dim), (state[..., 1] - state[..., 7]).
                              unsqueeze(dim = last_dim)], dim=last_dim), 2, dim=last_dim, keepdim=True) # (100, 20, 1)
    new_state = torch.cat([dg, vx, vy, px1, py1, vx1, vy1, da], dim=last_dim)

    return new_state, rot # (100, 20, 9) 



# def mlp(input_dim, mlp_dims, last_relu=False):
#     layers = []
#     mlp_dims = [input_dim] + mlp_dims
#     for i in range(len(mlp_dims) - 1):
#         layers.append(nn.Linear(mlp_dims[i], mlp_dims[i + 1]))
#         if i != len(mlp_dims) - 2 or last_relu:
#             layers.append(nn.ReLU())
#     net = nn.Sequential(*layers)
#     return net
