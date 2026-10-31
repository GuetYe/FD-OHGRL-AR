"""Categorical on-policy PPO using the original OHGRL graph encoder.

Behavior probabilities and values are captured before interaction. Dropout is
disabled for BOTH collection and optimization; eval() does not disable gradients.
"""
import numpy as np
import torch
from torch.distributions import Categorical
import setting
from algorithms_model import HighActor, HighCritic, gen_high_act_input_batch


def gae(rewards, values, next_values, gamma, lam):
    advantage = np.zeros(len(rewards), dtype=np.float32)
    carry = 0.0
    # Continuous control task: batch end is a truncation, not a terminal state.
    for i in reversed(range(len(rewards))):
        delta = rewards[i] + gamma * next_values[i] - values[i]
        carry = delta + gamma * lam * carry
        advantage[i] = carry
    return advantage, advantage + np.asarray(values, dtype=np.float32)


class OnPolicyPPO:
    def __init__(self, cfg):
        self.cfg = cfg
        self.generation = 0
        self.actor = HighActor(len(setting.edge_attr_choice), setting.high_hid_dim,
                               len(cfg['destinations']), setting.high_dropout).to(setting.device).eval()
        self.critic = HighCritic(0, len(setting.edge_attr_choice), setting.high_hid_dim,
                                 setting.high_dropout).to(setting.device).eval()
        self.actor_optimizer = torch.optim.Adam(self.actor.parameters(), lr=setting.high_actor_lr)
        self.critic_optimizer = torch.optim.Adam(self.critic.parameters(), lr=setting.high_critic_lr)

    def distribution(self, states, actor=None):
        actor = self.actor if actor is None else actor
        actor.eval()
        return Categorical(probs=actor(gen_high_act_input_batch(states).to(setting.device), deterministic=True))

    def values(self, states):
        self.critic.eval()
        return self.critic(gen_high_act_input_batch(states).to(setting.device)).reshape(-1)

    def behavior(self, state, actor, deterministic=False):
        with torch.no_grad():
            dist = self.distribution([state], actor)
            action = dist.probs.argmax(-1) if deterministic else dist.sample()
            return dict(action=int(action.item()), old_log_prob=float(dist.log_prob(action).item()),
                        value=float(self.values([state]).item()),
                        probabilities=dist.probs[0].cpu().tolist(), generation=self.generation)

    def update(self, rollout):
        if not rollout or any(t['generation'] != self.generation for t in rollout):
            raise ValueError('empty, stale or mixed-policy PPO rollout')
        for a, b in zip(rollout, rollout[1:]):
            if a['next_state'] is not b['state']:
                raise ValueError('GAE requires consecutive state transitions')
        states = [t['state'] for t in rollout]
        actions = torch.tensor([t['action'] for t in rollout], device=setting.device)
        old_log = torch.tensor([t['old_log_prob'] for t in rollout], device=setting.device)
        with torch.no_grad():
            initial = self.distribution(states).log_prob(actions)
            error = (initial - old_log).abs().max().item()
            if error > 1e-4:
                raise ValueError('behavior policy changed before PPO update')
        advantages, returns = gae([t['reward'] for t in rollout], [t['value'] for t in rollout],
                                  [t['next_value'] for t in rollout], setting.high_gamma, self.cfg['gae_lambda'])
        advantages = torch.tensor(advantages, device=setting.device)
        returns = torch.tensor(returns, device=setting.device)
        if len(rollout) > 1:
            advantages = (advantages - advantages.mean()) / (advantages.std(unbiased=False) + 1e-8)
        totals = dict(actor_loss=0., critic_loss=0., entropy=0., approximate_kl=0., clip_fraction=0.)
        draws = 0
        for epoch in range(self.cfg['ppo_epochs']):
            for ids in torch.randperm(len(rollout)).split(self.cfg['ppo_minibatch']):
                index = ids.to(setting.device)
                batch = [states[i] for i in ids.tolist()]
                dist = self.distribution(batch)
                log_ratio = dist.log_prob(actions[index]) - old_log[index]
                ratio = log_ratio.exp()
                advantage = advantages[index]
                unclipped = ratio * advantage
                clipped = ratio.clamp(1-self.cfg['ppo_clip'], 1+self.cfg['ppo_clip']) * advantage
                entropy = dist.entropy().mean()
                actor_loss = -torch.minimum(unclipped, clipped).mean() - self.cfg['entropy_coef'] * entropy
                critic_loss = (self.values(batch) - returns[index]).square().mean()
                if not torch.isfinite(actor_loss + critic_loss):
                    raise FloatingPointError('nonfinite PPO loss')
                self.actor_optimizer.zero_grad(); self.critic_optimizer.zero_grad()
                actor_loss.backward(); critic_loss.backward()
                torch.nn.utils.clip_grad_norm_(self.actor.parameters(), self.cfg['max_grad_norm'], error_if_nonfinite=True)
                torch.nn.utils.clip_grad_norm_(self.critic.parameters(), self.cfg['max_grad_norm'], error_if_nonfinite=True)
                self.actor_optimizer.step(); self.critic_optimizer.step()
                metrics = dict(actor_loss=actor_loss.item(), critic_loss=critic_loss.item(), entropy=entropy.item(),
                               approximate_kl=((ratio-1)-log_ratio).mean().item(),
                               clip_fraction=((ratio-1).abs()>self.cfg['ppo_clip']).float().mean().item())
                for k, v in metrics.items(): totals[k] += v * len(ids)
                draws += len(ids)
        self.generation += 1
        return dict({k:v/draws for k,v in totals.items()}, sample_passes=draws,
                    rollout_size=len(rollout), initial_log_probability_error=error)
