# LEARNING TO REPEAT:  
FINE GRAINED ACTION REPETITION FOR DEEP REINFORCEMENT LEARNING

Sahil Sharma, Aravind S. Lakshminarayanan, Balaraman Ravindran  
Indian Institute of Technology, Madras  
Chennai, 600036, India  
{sahil, ravi}@cse.iitm.ac.in  
aravindsrinivas@gmail.com

# ABSTRACT

Reinforcement Learning algorithms can learn complex behavioral patterns for sequential decision making tasks wherein an agent interacts with an environment and acquires feedback in the form of rewards sampled from it. Traditionally, such algorithms make decisions, i.e., select actions to execute, at every single time step of the agent-environment interactions. In this paper, we propose a novel framework, Fine Grained Action Repetition (FiGAR), which enables the agent to decide the action as well as the time scale of repeating it. FiGAR can be used for improving any Deep Reinforcement Learning algorithm which maintains an explicit policy estimate by enabling temporal abstractions in the action space. We empirically demonstrate the efficacy of our framework by showing performance improvements on top of three policy search algorithms in different domains: Asynchronous Advantage Actor Critic in the Atari 2600 domain, Trust Region Policy Optimization in Mujoco domain and Deep Deterministic Policy Gradients in the TORCS car racing domain.

# 1 INTRODUCTION

Reinforcement learning (RL) is used to solve goal-directed sequential decision making problems wherein explicit supervision in the form of correct decisions is not provided to the agent, but only evaluative feedback in the form of the rewards sampled from the environment. RL algorithms model goal-directed sequential decision making problems as Markov Decision Processes (MDP) [Sutton & Barto (1998)]. However, for problems with an exponential or continuous state space, tabular RL algorithms that maintain value or policy estimates for every state become infeasible. Therefore, there is a need to be able to generalize decision making to unseen states. Recent advances in representation learning through deep neural networks provide an efficient mechanism for such generalization [LeCun et al. (2015)]. Such a combination of representation learning through deep neural networks with reinforcement learning objectives has shown promising results in many sequential decision making domains such as the Atari 2600 domain [Bellemare et al. (2013); Mnih et al. (2015); Schaul et al. (2015); Mnih et al. (2016b)], Mujoco simulated physics tasks domain [Todorov et al. (2012); Lillicrap et al. (2015)], the Robosoccer domain [Hausknecht et al. (2016)] and the TORCS domain [Wymann et al. (2000); Mnih et al. (2016b)]. Often, MDP settings consist of an agent interacting with the environment at discrete time steps. A common feature shared by all the Deep Reinforcement Learning (DRL) algorithms above is that they repeatedly execute a chosen action for a fixed number of time steps  $k$ . If  $a_{t}$  represents the action taken at time step  $t$ , then for the said algorithms,  $a_{1} = a_{2} = \dots = a_{k}, a_{k + 1} = a_{k + 2} = \dots = a_{2k}$  and in general  $a_{ik + 1} = a_{ik + 2} = \dots = a_{(i + 1)k}, i \geq 0$ . Action repetition allows these algorithms to compute the action once every  $k$  time steps and hence operate at higher speeds, thus achieving real-time performance. This also offers other advantages such as smooth action policies. More importantly, as shown in Lakshminarayanan et al. (2016) and Durugkar et al. (2016), macro-actions constituting the same action repeated  $k$  times could be interpreted as introducing temporal abstractions in the induced policies thereby enabling transitions between temporally distant advantageous states.

![](images/0f10bf5be177323b4c316eefe9e68e47389c9dcc35ac2dcce0751c8f33a2c4fb.jpg)

![](images/ef83b25b3f7a54c19f611af95d4f985be92966495003b4549032319c6705dd33.jpg)  
(b) Sea Quest  
Figure 1: FiGAR induces temporal abstractions in learnt policies. The arrows indicate the action executed between the frames and the numbers depict the number of time steps for which the action was repeated. The thunder bolt corresponds to the firing action. An arrow alongside a thunderbolt corresponds to the action (arrow+fire). In the figure (a), the agent learns to execute down operation (which is equivalent to a no-op in this particular state, in this game) until a traveling car passes by and then executes temporally elongated actions to complete the task, skillfully avoiding the red car in the  $7^{th}$  frame. In figure (b) the agent catches a glimpse of a pink opponent towards bottom right in the  $2^{nd}$  frame and executes temporally elongated actions to intercept and kill it (in the  $6^{th}$  frame.

The time scale for action repetition has largely been static in DRL algorithms until now [Mnih et al. (2015; 2016b); Schaul et al. (2015)]. Lakshminarayanan et al. (2016) are the first to explore dynamic time scales for action repetition in the DRL setting and show that it leads to significant improvement in performance on a few Atari 2600 games. However, they choose only two time scales and the experiments are limited to a few representative games. Moreover the method is limited to tasks with a discrete action space.

We propose FiGAR, a framework that enables any DRL algorithm regardless of whether its action space is continuous or discrete, to learn temporal abstractions in the form of temporally extended macro-actions. FiGAR uses a structured and factored representation of the policy whereby the policy for choosing the action is decoupled from that for the action repetition selection. Note that deciding actions and the action repetitions independently enables us to find temporal abstractions without blowing up the action space, unlike Mnih et al. (2016a) and Lakshminarayanan et al. (2016). The contribution of this work is twofold. First, we propose a generic extension to DRL algorithms by coming up with a factored policy representation for temporal abstractions (see figure 1 for sequences of macro actions learnt in 2 Atari 2600 games). Second, we empirically demonstrate FiGAR's efficiency in improving policy gradient DRL algorithms with improvements in performance over several domains: 31 Atari 2600 games with Asynchronous Advantage Actor Critic [Mnih et al. (2016b)], 5 tasks in MuJoCo Simulated physics tasks domain with Trust Region Policy Optimization [Schulman et al. (2015)] and the TORCS domain with Deep Deterministic Policy Gradients [Lillicrap et al. (2015)].

# 2 RELATED WORK

Our framework is centered on a very general idea of deciding when necessary. There have been similar ideas outside the RL domains. For instance, Gu et al. (2016) and Satija & Pineau (2016) explore Real Time Neural Machine Translation where the action at every time step is to decide whether to output a new token in the target language or not based on current context.

Transition Point Dynamic Programming (TPDP) [Buckland & Lawrence (1994)] algorithm is a modification to the tabular dynamic programming paradigm that can reduce the learning time and memory required for control of continuous stochastic dynamic systems. This is done by determining a set of transition points in the underlying MDP. The policy changes only at these transition point states. The algorithm learns an optimal set of transition point states by using a variant of Q-Learning to evaluate whether or not to add/delete a particular state from the set of transition points. FiGAR learns the transition points in the underlying MDP on the fly with generalization across the state space unlike TPDP which is tabular and infeasible for large problems.

The Dynamic Frameskip Deep Q-network [Lakshminarayanan et al. (2016)] proposes to use multiple time scales of action repetition by augmenting the Deep Q Network (DQN) [Mnih et al. (2015)] with separate streams of the same primitive actions corresponding to each time scale. This way, the time scale of action repetition is dynamically learned. Although this framework leads to a significant improvement in the performance on a few Atari 2600 games, it suffers from not being able to support multiple time scales due to potential explosion of the action space and is restricted to discrete action spaces. Durugkar et al. (2016) also explore learning macro-actions composed using the same action repeated for different time scales. However, their framework is limited to discrete action spaces and performance improvements are not significant.

Learning temporally extended actions and abstractions have been of interest in RL for a long time. Mnih et al. (2016a) propose Strategic Attentive Writer (STRAW) for learning macro-actions and building dynamic action-plans directly from reinforcement learning signals. Instead of outputting a single action after each observation, STRAW maintains a multi-step action plan. The agent periodically updates the plan based on observations and commits to the plan between the re-planning steps. Although the STRAW framework represents a more general temporal abstraction than FiGAR, FiGAR should be seen as a framework that can compliment STRAW whereby the decision to repeat could now be hierarchical at plan and base action levels.

FiGAR is a framework that has a structured policy representation where the time scale of execution could be thought as parameterizing the chosen action. The only other work that explores parameterized policies in DRL is Hausknecht & Stone (2016) where discrete actions are parameterized by continuous values. In our case, discrete/continuous actions are parameterized by discrete values. The state spaces in Atari are also more sophisticated than the kind explored in Hausknecht et al. (2016).

# 3 BACKGROUND

# 3.1 ASYNCHRONOUS ADVANTAGE ACTOR CRITIC

Actor critic algorithms execute policy gradient updates by maintaining parametric estimates for the policy  $\pi_{\theta_a}(a|s)$  and the value function  $V_{\theta_c}(s)$  [Sutton & Barto (1998)]. The value function estimates are used to reduce the variance in the policy gradient updates.

Asynchronous Advantage Actor Critic (A3C) [Mnih et al. (2016b)] learns policies based on an asynchronous  $n$ -step returns. The  $k$  learner threads execute  $k$  copies of the policy asynchronously and the parameter updates are sent to a central parameter server at regular intervals. This ensures that temporal correlations are broken between subsequent updates since the different threads possibly explore different parts of the state space in parallel. The objective function for policy improvement in A3C is:

$$
L \left(\theta_ {a}\right) = \log \pi_ {\theta_ {a}} \left(a _ {t} \mid s _ {t}\right) \left(G _ {t} - V \left(s _ {t}\right)\right)
$$

where  $G_{t}$  is an estimate for the return at time step  $t$ . The A3C algorithm uses  $n$ -step returns for estimating  $G_{t}$  which is a biased estimate for  $Q(s_{t},a_{t})$ . Hence one can think of  $G_{t} - V(s_{t})$  as an estimate for  $A(s_{t},a_{t})$  which represents the advantage of taking action  $a_{t}$  in state  $s_{t}$ . The value function  $V_{\theta_c}(s_t)$  is updated by using  $n$ -step TD error as:  $L(\theta_c) = \left(\hat{V} (s_t) - V_{\theta_c}(s_t)\right)^2$  where  $\hat{V} (s_t)$  is an estimate of the  $n$ -step return from the current state. In A3C  $j$ -step returns are used where  $j\leq n$  and  $n$  is a fixed hyper-parameter. For simplicity assume that  $t\leq n$ . Then the definition for  $\hat{V} (s_t)$  is:

$$
\hat {V} (s _ {t}) = \sum_ {j = t} ^ {n - 1} \gamma^ {t - j} r _ {j} + \gamma^ {n - t} V (s _ {n})
$$

Algorithm 1 Create  $FiGAR - Z$  
1: function MAKEFIGAR(DRLAlgorithm Z, ActionRepetitionSet R)  
2:  $s_t \gets$  state at time t  
3:  $a_t \gets$  action taken in  $s_t$  at time t  
4:  $\pi_a \gets$  action policy of Z  
5:  $f_{\theta_a}(s_t) \gets$  action network for realizing action policy  $\pi_a$   
6:  $L(\pi_a, s_t, a_t) \gets$  A's objective function for improving  $\pi_a$   
7:  $\pi_x \gets$  construct action repetition policy for FiGAR-Z.  
8:  $f_{\theta_x}(s_t) \gets$  repetition network with output of size  $|R|$  for action repetition policy  $\pi_x$ .  
9:  $L(\pi_x, s_t, a_t) \gets$  L evaluated at  $\pi_x$   
10:  $T(s_t, a_t) \gets L(\pi_x, s_t, a_t) * L(\pi_a, s_t, a_t) // Total Loss$   
11: return T,  $f_{\theta_a}, f_{\theta_x}$

The policy and value functions are parameterized by Deep Neural Networks.

# 3.2 TRUST REGION POLICY OPTIMIZATION

TRPO [Schulman et al. (2015)] is a policy optimization algorithm. Constrained optimization of a surrogate loss function is proposed, with theoretical guarantees for monotonic policy improvement. The TRPO surrogate loss function  $L$  for potential next policies  $(\tilde{\pi})$  is:

$$
L _ {\theta_ {o l d}} (\tilde {\theta}) = \eta (\pi) + \sum_ {s} \rho^ {\pi} (s) \sum_ {a} \tilde {\pi} (a | s) A _ {\pi} (s, a)
$$

where  $\theta_{old}$  are the parameters of policy  $\pi$  and  $\tilde{\theta}$  are parameters of  $\tilde{\pi}$ . This surrogate loss function is optimized subject to the constraint:

$$
D _ {K L} ^ {\max } (\pi , \tilde {\pi}) \leq \delta
$$

which ensures that the policy improvement can be done in non-trivial step sizes and at the same time the new policy does not deviate much from the current policy due to the KL-divergence constraint.

# 3.3 DEEP DETERMINISTIC POLICY GRADIENTS

According to the Deterministic Policy Gradient (DPG) Theorem [Lever (2014)], the gradient of the performance objective  $(J)$  of the deterministic policy  $(\mu)$  in continuous action spaces with respect to the policy parameters  $(\theta)$  is given by:

$$
\begin{array}{l} \nabla_ {\theta} J (\mu_ {\theta}) = \int_ {\mathbb {S}} \rho^ {\mu} (s) \nabla_ {\theta} \mu_ {\theta} (s) \nabla_ {a} Q ^ {\mu} (s, a) | _ {a = \mu_ {\theta} (s)} d s \tag {1} \\ = \mathbb {E} _ {s \sim \rho^ {\mu}} [ \nabla_ {\theta} \mu_ {\theta} (s) \nabla_ {a} Q ^ {\mu} (s, a) | _ {a = \mu_ {\theta} (s)} ] \\ \end{array}
$$

for an appropriately defined performance objective  $J$ . The DPG model built according to this theorem consists of an actor which outputs an action vector in the continuous action space and a critic model  $Q(s, a)$  which evaluates the action chosen at a state. The DDPG algorithm [Lillicrap et al. (2015)] extends the DPG algorithm by introducing non-linear neural network based function approximators for the actor and critic.

# 4 FIGAR: FINE GRAINED ACTION REPETITION

FiGAR provides a DRL algorithm with the ability to model temporal abstractions by augmenting it with the ability to predict the number of time steps for which an action chosen for execution is to be repeated. This prediction is conditioned on the current state in the environment.

The FiGAR framework can be used to extend any DRL algorithm (say  $Z$ ) which maintains an explicit policy. Let  $Z'$  denote the extension of  $Z$  under FiGAR.  $Z'$  has two independent decoupled policy components. The policy  $\pi_{\theta_a}$  for choosing actions and the policy  $\pi_{\theta_x}$  for choosing action repetitions. Algorithm 1 describes the generic framework for deriving DRL algorithm  $Z'$  from algorithm  $Z$ . Let  $R$  stand for the set of all action repetitions that  $Z'$  would be able to perform. In

tradition DRL algorithms,  $R = \{4\}$ , that is, action repetition is static and fixed. In FiGAR, The set of action repetitions from which  $Z'$  can choose is  $R = \{r_1, r_2, \dots, r_{|R|}\}$ . The central idea behind FiGAR is that the objective function used to update the parameters  $\theta_a$  of  $\pi_{\theta_a}$  maintained by  $Z$  will be used to update the parameters  $\theta_x$  of the action repetition policy  $\pi_{\theta_x}$  of  $Z'$  as well (illustrated by the sharing of  $L$  in Algorithm 1). In the first sub-section, we describe how  $Z'$  operates. In the next two sub-sections, we describe the instantiations of FiGAR extensions for 3 policy gradient DRL algorithms: A3C, TRPO and DDPG.

# 4.1 HOW FIGAR OPERATES

The following procedure describes how FiGAR variant  $Z'$  navigates the MDP that it is solving:

1. In the very first state  $s_0$  seen by  $Z'$ , it predicts a tuple  $(a_0, x_0)$  of action to execute and number of time steps for which to execute it.  $a_0$  is decided based on  $\pi_{\theta_a}(s_0)$  whereas  $x_0$  is decided based on  $\pi_{\theta_x}(s_0)$ . Each such tuple is known as an action decision.  
2. We denote by  $s_j$  the state of the agent after  $j$  such action decisions have been made. Similarly  $x_j$  and  $a_j$  denote the action repetition and the action chosen after  $j$  such action decisions. Note that  $x_j \in \{r_1, r_2, \dots, r_{|R|}\}$ , the set of all allowed action repetitions.  
3. From time step 0 until  $x_0$ ,  $Z'$  executes  $a_0$ .  
4. At time step  $x_0$ ,  $Z'$  again decides, based on current state  $s_1$  and policy components  $(\pi_{\theta_a}(s_1), \pi_{\theta_x}(s_1))$ , the tuple of action to execute and the number of times for which to execute it,  $(a_1, x_1)$ .  
5. It can be seen that in general if  $Z'$  executes action  $a_k$  for  $x_k$  successive time steps, the next action is decided at time step  $t = \sum_{i=0}^{k} x_i$  on the basis of  $(\pi_{\theta_a}(s_{k+1}), \pi_{\theta_x}(s_{k+1}))$ , where  $s_{k+1}$  is the state seen at time step  $t$ .

# 4.2 FIGAR-A3C

A3C uses  $f_{\theta_a}(s_j)$  and  $f_{\theta_c}(s_j)$  which represent the policy  $\pi(a|s_j)$  and the value function  $V(s_j)$  respectively.  $\pi(a|s_j)$  is a vector of size equal to the action space of the underlying MDP while  $V(s_j)$  is a scalar. FiGAR extends the A3C algorithm as follows:

1. With  $s_j$  defined as in the previous sub-section, in addition to  $f_{\theta_a}(s_j)$  and  $f_{\theta_c}(s_j)$ , FiGAR-A3C defines a neural network  $f_{\theta_x}(s_j)$ . This neural network outputs a  $|R|$ -dimensional vector representing the probability distribution over the elements of the set  $R$ . The sampled time scale from this multinomial distribution decides how long the action decided with  $f_{\theta_a}(s_j)$  is repeated. The actor is now composed of both  $f_{\theta_a}(s_j)$  (action network) and  $f_{\theta_x}(s_j)$  (repetition network).

2. The objective function for the actor is modified to be:

$$
L (\theta_ {a}, \theta_ {x}) = (\log f _ {\theta_ {a}} (a | s _ {j}) + \log f _ {\theta_ {x}} (x | s _ {j})) A (s _ {j}, a, x)
$$

where  $A(s_{j},a,x)$  represents the advantage of executing action  $a$  for  $x$  time steps at state  $s_j$ . This implies that for FiGAR-A3C the combination operator * defined in Algorithm 1 is in fact scalar addition.

3. The objective function for the critic is the same except that estimated value function used in the target for the critic is changed as:

$$
\hat {V} (s _ {j}) = \sum_ {k = j} ^ {n - 1} \gamma^ {y _ {k - j}} r _ {k} + \gamma^ {y _ {n - j}} V (s _ {n})
$$

where we define  $y_0 = 0, y_k = y_{k-1} + x_k, k \geq 1$  and action  $a_k$  was repeated  $x_k$  times when state  $s_k$  was encountered. Note that the return used in target is based on  $n$  decision steps, steps at which a potential change in actions executed takes place. It is not based on  $n$  time steps.

Note that point 2 above implies that the action space has been extended by  $|R|$  and has a dimension of  $|A| + |R|$ . It is only because of this factored representation of the FiGAR policy that the number

of parameters do not blow up. If one were to extend the action space in a naive way by coupling the actions and the action repetitions, one would end up suffering the kind of action-space blow-up as seen in [Lakshminarayanan et al. (2016); Mnih et al. (2016a)] wherein for being able to control with respect to  $|R|$  different action repetition levels (or  $|R|$ -length policy plans in the case of STRAW), one would need to model  $|A| \times |R|$  actions or action-values which would blow up the final layer size  $|R|$  times.

# 4.3 FIGAR-TRPO

Although  $f_{\theta_a}(s_j)$  in A3C is generic enough to output continuous or discrete actions, we consider A3C only for discrete action spaces. Preserving the notation from the previous subsection, we describe FiGAR-TRPO where we consider the case of the output generated by the network  $f_{\theta_a}(s_j)$  to be  $A$  dimensional with each dimension being independent and describing a continuous valued action. The stochastic policy is hence modeled as a multi-variate Gaussian with diagonal co-variance matrix. The parameters of the mean as well as the co-variance matrix are together represented by  $\theta_a$  and the concatenated mean-co-variance vector is represented by the function  $f_{\theta_a}(s_j)$ . FiGAR-TRPO is constructed as follows:

1. In TRPO, the objective function  $L_{\theta_{ol,d}}(\tilde{\theta})$  is constructed based on trajectories drawn according to the current policy. Hence, for FiGAR-TRPO the objective function is modified to be:

$$
L _ {\theta_ {a, o l d}, \theta_ {x, o l d}} (\tilde {\theta} _ {a}) \times \left(L _ {\theta_ {a, o l d}, \theta_ {x, o l d}} (\tilde {\theta} _ {x})\right) ^ {\beta_ {a r}}
$$

where  $\theta_{x}$  are the parameters of sub-network  $f_{\theta_x}$  which computes the action repetition distribution. This implies that for FiGAR-TRPO the combination operator  $*$  defined in Algorithm 1 is in some sense the scalar multiplication.  $\beta_{ar}$  controls the relative learning rate of the core-policy parameters and the action repetition parameters.

2. The constraint in TRPO corresponding to the KL divergence between old and new policies is modified to be:

$$
D _ {K L} ^ {\max } \left(\pi_ {a}, \tilde {\pi} _ {a}\right) + \beta_ {K L} D _ {K L} ^ {\max } \left(\pi_ {x}, \tilde {\pi} _ {x}\right) \leq \delta
$$

where  $\pi_{a}$  denotes the Gaussian distribution for the action to be executed and  $\pi_{x}$  denotes the multinomial softmax-based action repetition probability distribution.  $\beta_{KL}$  controls the relative divergence of  $\pi_{x}$  and  $\pi_{a}$  from the new corresponding policies. See Appendix  $C$  for an explanation of the loss function used.

# 4.4 FIGAR-DDPG

In this subsection, we present an extension of DDPG under the FiGAR framework. DDPG consists of  $f_{\theta_a}(s_j)$  which denotes a deterministic policy  $\mu(s)$  and is a vector of size equal to the action space of the underlying MDP; and  $f_{\theta_c}(s_j, a_j)$  which denotes the critic network whose output is a single number, the estimated state-action value function  $Q(s_j, a_j)$ . FiGAR framework extends the DDPG algorithm as follows:

1.  $f_{\theta_x}$  is introduced, similar to FiGAR-A3C. This implies that the complete policy for FiGAR-DDPG  $(\pi_{\theta_a}, \pi_{\theta_x})$  is computed by the tuple of neural networks:  $(f_{\theta_a}, f_{\theta_x})$ . DDPG does not have an explicit loss function for the Actor network. The gradient back-propagated from the Critic can be thought of as the loss function for the Actor. In this case the "loss" for the total policy is simply the concatenation of the gradients with respect to  $f_{\theta_a}$  and  $f_{\theta_x}$ . This implies that for FiGAR-DDPG the combination operator * defined in Algorithm 1 is the vector concatenation operator.  
2. To ensure sufficient exploration, the exploration policy for action repetition is an  $\epsilon$ -greedy version of the behavioral action repetition policy. The action part of the policy,  $(f_{\theta_a}(s_j))$ , continues to use temporally correlated noise for exploration, generated by an Ornstein-Uhlenbeck process (see Lillicrap et al. (2015) for details).  
3. The critic is modeled by the equation

$$
f \left(s _ {j}, a _ {j}, x _ {j}\right) = f _ {\theta_ {c}} \left(s _ {j}, f _ {\theta_ {a}} \left(s _ {j}\right), f _ {\theta_ {x}} \left(s _ {j}\right)\right)
$$

As stated above,  $f_{\theta_x}$  is learnt by back-propagating the gradients produced by the critic with respect to  $f_{\theta_x}$ , in exactly the same way that  $f_{\theta_a}$  is learnt.

# 5 EXPERIMENTAL SETUP AND RESULTS

The experiments are designed to understand the answers to the following questions:

1. For different DRL algorithms, can FiGAR extensions learn to use the dynamic action repetition?  
2. How does FiGAR impact the performance of the different algorithms on various tasks?  
3. Is FiGAR able to learn control on several different kinds of Action Repetition sets  $R$ ?

![](images/a45fd86c613ae772e60a7133ce4a7feffa3d46df9416a6688982c3e680fce9df.jpg)  
Figure 2: Improvement by FiGAR-A3C over A3C for Atari 2600

In the next two sub-sections, we experiment with the simplest possible action repetition set  $R = \{1,2,\dots ,|R|\}$ . In the third sub-section, we understand the effects that changing the action repetition set  $R$  has on the policies learnt.

# 5.1 FIGAR-A3C ON ATARI 2600

This set of experiments was performed with FiGAR-A3C on the Atari 2600 domain. The hyperparameters were tuned on a subset of games (Beamrider, Breakout, Pong, Seaquest and Space Invaders) and kept constant across all games.

$R$  is perhaps the most important hyper-parameter and depicts our confidence in the ability of a DRL agent to predict the future. Such a choice has to depend on the domain in which the DRL agent is operating. We only wanted to demonstrate the ability of FiGAR to learn temporal abstractions and hence instead of tuning for an optimal  $R$ , it was chosen to be 30, arbitrarily. The specific set of time scales we choose is  $1,2,3,\dots,30$ . FiGAR-A3C as well as A3C were trained for 100 million decision steps. They were evaluated in terms of the final policy learnt. Treating the score obtained by the A3C algorithm as baseline (b), we calculated the percentage improvement (i) offered by

FiGAR-A3C (f) as:  $i = \frac{f - b}{b}$ . Figure 2 plots this metric versus the game names. The improvement for Atlantis is staggering and more than  $3500\%$ . Figure 2's y-axis has been clipped at  $1000\%$  to make it more presentable. Appendix A contains the experimental details, the raw scores obtained by both the methods. Appendix B contains experiments on validating our setup.

Table 1: Evaluation of Action Repetition Control for Atari 2600. See Appendix B (Table 6) for an expanded version of this table.  

<table><tr><td>Name</td><td>1-3</td><td>4-6</td><td>7-9</td><td>10-12</td><td>13-15</td><td>16-18</td><td>19-21</td><td>22-24</td><td>25-27</td><td>28-30</td></tr><tr><td>Atlantis</td><td>0.51</td><td>0.07</td><td>0.18</td><td>0.08</td><td>0.02</td><td>0.02</td><td>0.01</td><td>0.02</td><td>0.03</td><td>0.07</td></tr><tr><td>Crazy Climber</td><td>0.55</td><td>0.04</td><td>0.01</td><td>0.38</td><td>0.01</td><td>0.01</td><td>0.01</td><td>0.00</td><td>0.00</td><td>0.00</td></tr><tr><td>Demon Attack</td><td>0.16</td><td>0.35</td><td>0.14</td><td>0.12</td><td>0.08</td><td>0.05</td><td>0.05</td><td>0.03</td><td>0.01</td><td>0.02</td></tr><tr><td>Koolaid</td><td>0.36</td><td>0.15</td><td>0.19</td><td>0.06</td><td>0.06</td><td>0.06</td><td>0.05</td><td>0.02</td><td>0.03</td><td>0.04</td></tr><tr><td>Pong</td><td>0.19</td><td>0.16</td><td>0.13</td><td>0.13</td><td>0.06</td><td>0.09</td><td>0.04</td><td>0.05</td><td>0.07</td><td>0.10</td></tr><tr><td>Freeway</td><td>0.15</td><td>0.18</td><td>0.09</td><td>0.09</td><td>0.06</td><td>0.07</td><td>0.05</td><td>0.06</td><td>0.07</td><td>0.18</td></tr><tr><td>Sea Quest</td><td>0.59</td><td>0.19</td><td>0.06</td><td>0.02</td><td>0.02</td><td>0.03</td><td>0.05</td><td>0.03</td><td>0.01</td><td>0.00</td></tr><tr><td>Space Invaders</td><td>0.42</td><td>0.18</td><td>0.11</td><td>0.06</td><td>0.04</td><td>0.02</td><td>0.02</td><td>0.02</td><td>0.03</td><td>0.10</td></tr><tr><td>Tutankham</td><td>0.16</td><td>0.74</td><td>0.02</td><td>0.01</td><td>0.01</td><td>0.01</td><td>0.01</td><td>0.01</td><td>0.01</td><td>0.01</td></tr><tr><td>Wizard of Wor</td><td>0.28</td><td>0.12</td><td>0.08</td><td>0.19</td><td>0.11</td><td>0.08</td><td>0.04</td><td>0.04</td><td>0.04</td><td>0.02</td></tr></table>

To answer the first question we posed, experiments were conducted to record the percentage of times that a particular action repetition was chosen. Table 1 presents the action repetition distribution across a selection of games, chosen arbitrarily. The values have been rounded to 2 decimal places. Each game was played for 10 episodes using the same policy used to calculate average scores in Figure 2.

The two tables together show that FiGAR-A3C generally prefers lower action repetition but does come up with temporal abstractions in policy space (specially in games like Pong and Crazy Climber). Some such abstractions have been demonstrated in Figure 1. Such temporal abstractions do not always help general gameplay (Demon Attack). However, as can be seen from Figure 2, FiGAR-A3C outperforms A3C in 25 out of 31 games.

# 5.2 FIGAR-TRPO ON MUJOCO TASKS

In this sub-section we demonstrate that FiGAR-TRPO can learn to solve the Mujoco simulated physics tasks reasonably successfully. Similar to FiGAR-A3C,  $R$  is chosen to be 30 arbitrarily. The

Table 2: Evaluation of FiGAR on Mujoco  

<table><tr><td>Domain</td><td>FiGAR-TRPO</td><td>TRPO</td></tr><tr><td>Ant</td><td>947.06 (28.35)</td><td>-161.93 (1.00)</td></tr><tr><td>Hopper</td><td>3038.63 (1.00)</td><td>3397.58 (1.00)</td></tr><tr><td>Inverted Pendulum</td><td>1000.00 (1.00)</td><td>971.66 (1.00)</td></tr><tr><td>Inverted Double Pendulum</td><td>8712.46 (1.01)</td><td>8327.75 (1.00)</td></tr><tr><td>Swimmer</td><td>337.48 (10.51)</td><td>364.55 (1.00)</td></tr></table>

full policy  $(f_{\theta_a}, f_{\theta_x})$  is trained jointly. The policies learnt after each TRPO optimization step (details in Appendix  $C$ ) are compared to current best known policy to arrive at the overall best policy. The results in this sub-section are for this best policy. Table 2 compares the performance of TRPO and FiGAR-TRPO. The number in the brackets is the average action repetition chosen. As can be seen from the table, FiGAR learns either policies which are much faster to execute albeit at cost of slight loss in optimality or it learns policies similar to non-repetition case, performance being competitive with the baseline algorithm. This best policy was then evaluated on 100 episodes to arrive at average scores which are contained in Table 2. TRPO is a difficult baseline on the MuJoCo tasks domain. On the whole, FiGAR outperforms TRPO in 3 out of 5 domains, although the gains are marginal in

most tasks. Appendix  $C$  contains experimental details. A video showing FiGAR-TRPO's learned behavior policies can be found at http://youtu.be/JiaO2tBtH-k.

# 5.3 FIGAR-DDPG ON TORCS

FiGAR-DDPG was trained and tested on the TORCS domain.  $R$  was chosen to be 15 arbitrarily. FIGAR-DDPG manages to complete the race task flawlessly and manages to finish 20 laps of the circuit, after which the simulator stops. The total reward obtained by FiGAR-DDPG was 557929.68 as against 59519.70 obtained by DDPG. We also observed that FiGAR-DDPG learnt policies which were smoother than those learnt by DDPG. A video showing the learned driving behavior of the FiGAR-DDPG agent can be found at https://youtu.be/dX8J-sF-WX4. See Appendix  $D$  for experimental and architectural details.

# 5.4 AFFECT OF ACTION REPETITION SET ON FIGAR

This sub-section answers the third question raised at the beginning of this section in affirmative. We demonstrate that there is nothing sacrosanct about the set of action repetitions  $R = \{1,2,\dots ,30\}$  on which FiGAR-A3C performed well, and that the good performance carries over to other action repetition sets.

![](images/c8920f8b4f970ba891036f7a9a2edd028888043356aa783e60f3a0feddaa3cd5.jpg)  
Figure 3: Comparison of FiGAR-A3C variants to the A3C baseline for 3 games: Sea Quest, Space Invaders and Asterix. Game scores have been scaled down by 1000 and rounded to 1 decimal place.

To demonstrate the generality of FiGAR with respect to  $R$ , we chose a wide variety of action repetition sets  $R$ , trained and evaluated FiGAR-A3C variants which learn to repeat with respect to their respective Action Repetition sets. Table 3 describes the various FiGAR-variants considered for these experiments in terms of their action repetition set  $R$ .

Note that the hyper-parameters of the various variants of FiGAR-A3C were not tuned but rather the same ones obtained by tuning for FiGAR-30 were used. Figure 3 contains a comparison of the raw scores obtained by the various FiGAR-A3C variants in comparison to the A3C baseline. It is clear

Table 3: Description of FiGAR-A3C variants in terms of action repetition set  $R$  .  

<table><tr><td>Name</td><td>Description in terms of R</td></tr><tr><td>FiGAR-20</td><td>R = {1, 2, ···, 19, 20}</td></tr><tr><td>FiGAR-30</td><td>R = {1, 2, ···, 29, 30}</td></tr><tr><td>FiGAR-50</td><td>R = {1, 2, ···, 49, 50}</td></tr><tr><td>FiGAR-30-50</td><td>R = {30 numbers drawn randomly from R&#x27; = {1, 2, ···, 50} w/o replacement}</td></tr><tr><td>FiGAR-20-30</td><td>R = {20 numbers drawn randomly from R&#x27; = {1, 2, ···, 30} w/o replacement}</td></tr><tr><td>FiGAR-P</td><td>R = {p | p &lt; 50, p ∈ P (Set of all Primes)}</td></tr></table>

that FiGAR is able to learn over any action repetition set  $R$  and the performance does not fall by a lot even when hyper-parameters tuned for FiGAR-30 are used for other variants. Appendix  $E$  contains additional plots showing the evolution of average game scores against number of training steps.

# 6 CONCLUSION

We propose a light-weight framework (FiGAR) for improving current Deep Reinforcement Learning algorithms for policy optimization whereby temporal abstractions are learned in the policy space. The framework is generic and applicable to DRL algorithms concerned with policy gradients for continuous as well as discrete action spaces such as A3C, TRPO and DDPG. FiGAR maintains a structured policy wherein the action probability distribution is augmented with a probability distribution for choosing the time scale of repeating the chosen action. Our results demonstrate that FiGAR can be used to significantly improve the current policy gradient and Actor-Critic algorithms thereby learning better control policies across several domains by discovering optimal sequences of temporally elongated macro-actions.

# ACKNOWLEDGMENTS

We used the open source implementation of A3C at https://github.com/miyosuda/async_deep_reinforce. We thank Volodymr Mnih for giving valuable hyper-parameter information. We thank Aravind Rajeswaran (University of Washington) for very helpful discussions regarding and feedback on the MuJoCo domain tasks. The TRPO implementation was a modification of https://github.com/aravindr93/robustRL. The DDPG implementation was a modification of https://github.com/yanpanlau/DDPG-Keras-Torcs. We thank ILDS (http://web.iitm.ac.in/ilds/) for the compute resources we used for running A3C experiments.

# REFERENCES

Marc G. Bellemare, Yavar Naddaf, Joel Veness, and Michael Bowling. The arcade learning environment: An evaluation platform for general agents. Journal of Artificial Intelligence Research, pp. 253-279, June 2013.  
Kenneth M Buckland and Peter D Lawrence. Transition point dynamic programming. Advances in neural information processing systems, pp. 639-639, 1994.  
Ishan P Durugkar, Clemens Rosenbaum, Stefan Dernbach, and Sridhar Mahadevan. Deep reinforcement learning with macro-actions. arXiv preprint arXiv:1606.04615, 2016.  
Jiatao Gu, Graham Neubig, Kyunghyun Cho, and Victor OK Li. Learning to translate in real-time with neural machine translation. arXiv preprint arXiv:1610.00388, 2016.  
Matthew Hausknecht and Peter Stone. Deep reinforcement learning in parametrized action space. 4th International Conference on Learning Representations, 2016.  
Matthew Hausknecht, Prannoy Mupparaju, Sandeep Subramanian, Shivaram Kalyanakrishnan, and Peter Stone. Half field offense: An environment for multiagent learning and ad hoc teamwork. In AAMAS Adaptive Learning Agents (ALA) Workshop, May 2016.  
Kaiming He, Xiangyu Zhang, Shaoqing Ren, and Jian Sun. Delving deep into rectifiers: Surpassing human-level performance on imagenet classification. In Proceedings of the IEEE International Conference on Computer Vision, pp. 1026-1034, 2015.  
Aravind S Lakshminarayanan, Sahil Sharma, and Balaraman Ravindran. Dynamic frame skip deep q network. arXiv preprint arXiv:1605.05365, 2016.  
Yann LeCun, Yoshua Bengio, and Geoffrey Hinton. Deep learning. Nature, 521(7553):436-444, 2015.  
Guy Lever. Deterministic policy gradient algorithms. 2014.  
Timothy P Lillicrap, Jonathan J Hunt, Alexander Pritzel, Nicolas Heess, Tom Erez, Yuval Tassa, David Silver, and Daan Wierstra. Continuous control with deep reinforcement learning. arXiv preprint arXiv:1509.02971, 2015.  
Volodymyr Mniih, Koray Kavukcuoglu, David Silver, Andrei A. Rusu, Joel Veness, Marc G. Bellemare, Alex Graves, Martin Riedmiller, Andreas K. Fidjeland, Georg Ostrovski, Stig Petersen, Charles Beattie, Amir Sadik, Ioannis Antonoglou, Helen King, Dharshan Kumaran, Daan Wierstra, Shane Legg, and Demis Hassabis. Human-level control through deep reinforcement learning. Nature, February 2015.  
Volodymyr Mnih, John Agapiou, Simon Osindero, Alex Graves, Oriol Vinyals, Koray Kavukcuoglu, et al. Strategic attentive writer for learning macro-actions. arXiv preprint arXiv:1606.04695, 2016a.  
Volodymyr Mnih, Adria Puigdomenech Badia, Mehdi Mirza, Alex Graves, Timothy P Lillicrap, Tim Harley, David Silver, and Koray Kavukcuoglu. Asynchronous methods for deep reinforcement learning. arXiv preprint arXiv:1602.01783, 2016b.  
Harsh Satija and Joelle Pineau. Simultaneous machine translation using deep reinforcement learning. ICML 2016 Workshop on Abstraction in Reinforcement Learning, 2016.  
Tom Schaul, John Quan, Ioannis Antonoglou, and David Silver. Prioritized experience replay. 4th International Conference on Learning Representations, 2015.  
John Schulman, Sergey Levine, Philipp Moritz, Michael I Jordan, and Pieter Abbeel. Trust region policy optimization. CoRR, abs/1502.05477, 2015.  
Richard S. Sutton and Andrew G. Barto. Introduction to reinforcement learning. MIT Press, 1998.

Emanuel Todorov, Tom Erez, and Yuval Tassa. Mujoco: A physics engine for model-based control. In 2012 IEEE/RSJ International Conference on Intelligent Robots and Systems, pp. 5026-5033. IEEE, 2012.  
Bernhard Wymann, E Espie, C Guionneau, C Dimitrakakis, R Coulom, and A Sumner. Torcs, the open racing car simulator. Software available at http://torcs.sourceforge.net, 2000.
