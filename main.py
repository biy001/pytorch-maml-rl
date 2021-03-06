import maml_rl.envs
import gym
import numpy as np
import torch
import json
import sys
import pickle
import time
import timeit
from maml_rl.metalearner import MetaLearner
from maml_rl.policies import CategoricalMLPPolicy, NormalMLPPolicy
from maml_rl.baseline import LinearFeatureBaseline
from maml_rl.sampler import BatchSampler

from tensorboardX import SummaryWriter

def total_rewards(episodes_rewards, aggregation=torch.mean):
    rewards_total = torch.mean(torch.stack([aggregation(torch.sum(rewards[...,0], dim=0))
        for rewards in episodes_rewards], dim=0))
    rewards_dist = torch.mean(torch.stack([aggregation(torch.sum(rewards[...,1], dim=0))
        for rewards in episodes_rewards], dim=0))
    rewards_col = torch.mean(torch.stack([aggregation(torch.sum(rewards[...,2], dim=0))
        for rewards in episodes_rewards], dim=0))
    return rewards_total.item(), rewards_dist.item(), rewards_col.item()

def time_elapsed(elapsed_seconds):
    seconds = int(elapsed_seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    periods = [('hours', hours), ('minutes', minutes), ('seconds', seconds)]
    return  ', '.join('{} {}'.format(value, name) for name, value in periods if value)

def main(args):

    continuous_actions = (args.env_name in ['AntVel-v1', 'AntDir-v1',
        'AntPos-v0', 'HalfCheetahVel-v1', 'HalfCheetahDir-v1',
        '2DNavigation-v0', 'RVONavigation-v0', 'RVONavigationAll-v0'])

    assert continuous_actions == True

    writer = SummaryWriter('./logs/{0}'.format(args.output_folder))
    save_folder = './saves/{0}'.format(args.output_folder)
    log_traj_folder = './logs/{0}'.format(args.output_traj_folder)


    if not os.path.exists(save_folder):
        os.makedirs(save_folder)
    if not os.path.exists(log_traj_folder):
        os.makedirs(log_traj_folder)
    with open(os.path.join(save_folder, 'config.json'), 'w') as f:
        config = {k: v for (k, v) in vars(args).items() if k != 'device'}
        config.update(device=args.device.type)
        json.dump(config, f, indent=2)



    # log_reward_total_file = open('./logs/reward_total.txt', 'a')
    # log_reward_dist_file = open('./logs/reward_dist.txt', 'a')
    # log_reward_col_file = open('./logs/reward_col.txt', 'a')


    sampler = BatchSampler(args.env_name, batch_size=args.fast_batch_size,
        num_workers=args.num_workers)
    
    # print(sampler.envs.observation_space.shape)
    # print(sampler.envs.action_space.shape)

    # eewfe


    if continuous_actions:
        policy = NormalMLPPolicy(
            int(np.prod(sampler.envs.observation_space.shape)),
            int(np.prod(sampler.envs.action_space.shape)),
            hidden_sizes=(args.hidden_size,) * args.num_layers)
    else:
        policy = CategoricalMLPPolicy(
            int(np.prod(sampler.envs.observation_space.shape)),
            sampler.envs.action_space.n,
            hidden_sizes=(args.hidden_size,) * args.num_layers)
    # baseline = LinearFeatureBaseline(
    #     int(np.prod(sampler.envs.observation_space.shape)))
    baseline = LinearFeatureBaseline(int(np.prod((2,))))





    resume_training = True

    if resume_training:
        saved_policy_path = os.path.join('./TrainingResults/result2//saves/{0}'.format('maml-2DNavigation-dir'), 'policy-180.pt')
        if os.path.isfile(saved_policy_path):
            print('Loading a saved policy')
            policy_info = torch.load(saved_policy_path)
            policy.load_state_dict(policy_info)
        else:
            sys.exit("The requested policy does not exist for loading")
            

    metalearner = MetaLearner(sampler, policy, baseline, gamma=args.gamma,
        fast_lr=args.fast_lr, tau=args.tau, device=args.device)

    start_time = time.time()
    for batch in range(args.num_batches):
        tasks = sampler.sample_tasks(num_tasks=args.meta_batch_size)
        episodes = metalearner.sample(tasks, first_order=args.first_order)

        metalearner.step(episodes, max_kl=args.max_kl, cg_iters=args.cg_iters,
            cg_damping=args.cg_damping, ls_max_steps=args.ls_max_steps,
            ls_backtrack_ratio=args.ls_backtrack_ratio)

        # print("observations shape: ")
        # print(episodes[0][1].observations.shape)

        # ewerw

        # Tensorboard
        total_reward_be, dist_reward_be, col_reward_be = total_rewards([ep.rewards for ep, _ in episodes])
        total_reward_af, dist_reward_af, col_reward_af = total_rewards([ep.rewards for _, ep in episodes])

        log_reward_total_file = open('./logs/reward_total.txt', 'a')
        log_reward_dist_file = open('./logs/reward_dist.txt', 'a')
        log_reward_col_file = open('./logs/reward_col.txt', 'a')

        log_reward_total_file.write(str(batch)+','+str(total_reward_be)+','+str(total_reward_af)+'\n')
        log_reward_dist_file.write(str(batch)+','+str(dist_reward_be)+','+str(dist_reward_af)+'\n')
        log_reward_col_file.write(str(batch)+','+str(col_reward_be)+','+str(col_reward_af)+'\n')

        log_reward_total_file.close() # not sure if open and close immediantly will help save the appended logs in-place 
        log_reward_dist_file.close()
        log_reward_col_file.close()


        writer.add_scalar('total_rewards/before_update', total_reward_be, batch)
        writer.add_scalar('total_rewards/after_update', total_reward_af, batch)

        writer.add_scalar('distance_reward/before_update', dist_reward_be, batch)
        writer.add_scalar('distance_reward/after_update', dist_reward_af, batch)

        writer.add_scalar('collison_rewards/before_update', col_reward_be, batch)
        writer.add_scalar('collison_rewards/after_update', col_reward_af, batch)

        if batch % args.save_every == 0: # maybe it can save time/space if the models are saved only periodically
            # Save policy network
            print('Saving model {}'.format(batch))
            with open(os.path.join(save_folder,'policy-{0}.pt'.format(batch)), 'wb') as f:
                torch.save(policy.state_dict(), f)

        if batch % 30 == 0:
            with open(os.path.join(log_traj_folder, 'train_episodes_observ_'+str(batch)+'.pkl'), 'wb') as f: 
                pickle.dump([ep.observations.cpu().numpy() for ep, _ in episodes], f)
            with open(os.path.join(log_traj_folder, 'valid_episodes_observ_'+str(batch)+'.pkl'), 'wb') as f: 
                pickle.dump([ep.observations.cpu().numpy() for _, ep in episodes], f)

            # with open(os.path.join(log_traj_folder, 'train_episodes_ped_state_'+str(batch)+'.pkl'), 'wb') as f: 
            #     pickle.dump([ep.hid_observations.cpu().numpy() for ep, _ in episodes], f)
            # with open(os.path.join(log_traj_folder, 'valid_episodes_ped_state_'+str(batch)+'.pkl'), 'wb') as f: 
            #     pickle.dump([ep.hid_observations.cpu().numpy() for _, ep in episodes], f)
            # save tasks
            # a sample task list of 2: [{'goal': array([0.0209588 , 0.15981938])}, {'goal': array([0.45034602, 0.17282322])}]
            with open(os.path.join(log_traj_folder, 'tasks_'+str(batch)+'.pkl'), 'wb') as f: 
                pickle.dump(tasks, f)
            
        else:
            # supposed to be overwritten for each batch
            with open(os.path.join(log_traj_folder, 'latest_train_episodes_observ.pkl'), 'wb') as f: 
                pickle.dump([ep.observations.cpu().numpy() for ep, _ in episodes], f)
            with open(os.path.join(log_traj_folder, 'latest_valid_episodes_observ.pkl'), 'wb') as f: 
                pickle.dump([ep.observations.cpu().numpy() for _, ep in episodes], f)

            # with open(os.path.join(log_traj_folder, 'latest_train_episodes_ped_state.pkl'), 'wb') as f: 
            #     pickle.dump([ep.hid_observations.cpu().numpy() for ep, _ in episodes], f)
            # with open(os.path.join(log_traj_folder, 'latest_valid_episodes_ped_state.pkl'), 'wb') as f: 
            #     pickle.dump([ep.hid_observations.cpu().numpy() for _, ep in episodes], f)

            with open(os.path.join(log_traj_folder, 'latest_tasks.pkl'), 'wb') as f: 
                pickle.dump(tasks, f)

        print('finished epoch {}; time elapsed: {}'.format(batch,  time_elapsed(time.time() - start_time)))

    # log_reward_total_file.close() # didn't feel the need to call close()
    # log_reward_dist_file.close()
    # log_reward_col_file.close()

        # print(episodes[0][1].observations.shape) # the valid episode of the first task
        # print("FINISHED the first batch of meta-learning")
        # ewerfwe


if __name__ == '__main__':
    import argparse
    import os
    import multiprocessing as mp

    parser = argparse.ArgumentParser(description='Reinforcement learning with '
        'Model-Agnostic Meta-Learning (MAML)')

    # General
    parser.add_argument('--env-name', type=str, default='RVONavigationAll-v0',
        help='name of the environment')
    parser.add_argument('--gamma', type=float, default=0.9,
        help='value of the discount factor gamma')
    parser.add_argument('--tau', type=float, default=0.99,
        help='value of the discount factor for GAE')
    parser.add_argument('--first-order', action='store_true',
        help='use the first-order approximation of MAML')

    # Policy network (relu activation function)
    parser.add_argument('--hidden-size', type=int, default=100,
        help='number of hidden units per layer')
    parser.add_argument('--num-layers', type=int, default=2,
        help='number of hidden layers')

    # Task-specific
    parser.add_argument('--fast-batch-size', type=int, default=3, # 17
        help='batch size for each individual task')
    parser.add_argument('--fast-lr', type=float, default=0.1,
        help='learning rate for the 1-step gradient update of MAML')

    # Optimization
    parser.add_argument('--num-batches', type=int, default=200,
        help='number of batches')
    parser.add_argument('--meta-batch-size', type=int, default=1, #22
        help='number of tasks per batch')
    parser.add_argument('--max-kl', type=float, default=1e-2,
        help='maximum value for the KL constraint in TRPO')
    parser.add_argument('--cg-iters', type=int, default=10,
        help='number of iterations of conjugate gradient')
    parser.add_argument('--cg-damping', type=float, default=1e-5,
        help='damping in conjugate gradient')
    parser.add_argument('--ls-max-steps', type=int, default=15,
        help='maximum number of iterations for line search')
    parser.add_argument('--ls-backtrack-ratio', type=float, default=0.5,
        help='maximum number of iterations for line search')

    # Miscellaneous
    parser.add_argument('--output-folder', type=str, default='maml-2DNavigation-dir',
        help='name of the output folder')
    parser.add_argument('--output-traj-folder', type=str, default='2DNavigation-traj-dir',
        help='name of the output trajectory folder')
    parser.add_argument('--save_every', type=int, default=20,     
                        help='save frequency')
    parser.add_argument('--num-workers', type=int, default=8,
        help='number of workers for trajectories sampling')
    parser.add_argument('--device', type=str, default='cuda',
        help='set the device (cpu or cuda)')
    parser.add_argument('--resume_training', type=bool, default=False,
        help='if want to resume training from a saved policy')

    args = parser.parse_args()
    print(" ")
    print("--fast-lr: {}".format(args.fast_lr))
    print(" ")
    # on my laptop: mp.cpu_count() - 1 = 3

    # Create logs and saves folder if they don't exist
    if not os.path.exists('./logs'):
        os.makedirs('./logs')
    if not os.path.exists('./saves'):
        os.makedirs('./saves')
    # Device
    args.device = torch.device(args.device
        if torch.cuda.is_available() else 'cpu')
    # Slurm
    if 'SLURM_JOB_ID' in os.environ:
        args.output_folder += '-{0}'.format(os.environ['SLURM_JOB_ID'])

    main(args)
