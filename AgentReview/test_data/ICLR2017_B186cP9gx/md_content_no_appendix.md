# SINGULARITY OF THE HESSIAN IN DEEP LEARNING

# Levent Sagun

Mathematics Department

New York University

sagun@cims.nyu.edu

# Léon Bottou

Facebook AI Research

New York

leon@bottou.org

# Yann LeCun

Computer Science Department

New York University

yann@cs.nyu.edu

# ABSTRACT

We look at the eigenvalues of the Hessian of a loss function before and after training. The eigenvalue distribution is seen to be composed of two parts, the bulk which is concentrated around zero, and the edges which are scattered away from zero. We present empirical evidence for the bulk indicating how overparametrized the system is, and for the edges indicating the complexity of the input data.

# 1 INTRODUCTION

Given a (piece-wise) differentiable loss function, and a gradient based algorithm to minimize it, the knowledge of the second order information about it can tell us quite a bit about how the landscape looks like, and how we could modify our algorithm to make it go faster and find better solutions. But, one of the biggest challenges in second order optimization methods is in accessing that second order information itself. In particular, in deep learning there have been many proposals to accelerate training using second order information. Ngiam et al. (2011) has an in depth review of some of the proposals for approximating the Hessian of the loss function. Nevertheless, given the computational complexity of the problems at hand, we don't have much information on what the actual Hessian looks like. This work is part of a series of papers that explore the data-model-algorithm connection along with Sagun et al. (2014; 2015) and it builds on top of the intuition developed in Lecun et al. (1998).

In this short note, we show how the data and the architecture depends on the eigenvalues of the Hessian of the loss function. In particular, we observe that the top discrete eigenvalues depend on the data, and the bulk of the eigenvalues depend on the architecture. Furthermore, as we keep growing the size of the network, we observe that the discrete part that depends on data remains the same, but the concentration around zero sharpens.

There are various conclusions and implications of this singularity. Recent research suggest new insights into convergence properties of gradient based algorithms in non-convex systems (Lee et al., 2016; Hardt et al., 2015). The results come together with their implications on neural networks. However, the proofs require the system at hand to be non-degenerate. An immediate conclusion of our observation is that the Hessian of the loss function is very singular. Therefore, a lot of the theory and methodology that assumes non-singular Hessian cannot be applied without an appropriate modification.

# 2 MNIST EIGENVALUES ON A FULLY-CONNECTED MODEL

We calculate the exact Hessian of the loss function of a network with two hidden layers. The inputs are  $28 \times 28$  MNIST data, the network has two hidden layers with ReLU nonlinearity, the top layer has a softmax and a negative log likelihood loss function at the end. We plot the histogram of the eigenvalues of the Hessian for a varying number of hidden units. The Hessian turns out to be extremely singular, and increasing the number of units in hidden layers only add to the singularity of the Hessian.

![](images/72b110c15e35d079fd4be919c35b8e8004aac76c4b6665cc45301fb322bb9776.jpg)  
Figure 1: (left) Full Hessian matrix for one of the systems. (right) Eigenvalue profile for increasingly bigger networks.

# 2.1 VARYING THE DATA

To demonstrate how the eigenvalue distribution may depend on data itself, we keep the same architecture and change the inputs to random patterns. Initially, a random point in the weight space is selected, and we calculated the Hessian without any training. After training the system until the norm of the gradient is close to zero. We again calculate the exact Hessian and plot the histogram of their eigenvalues.

![](images/0d4aec1f047b1ac3cec12ac19650953ea794c56dc8dd8de9ee8baa2c15bf71a7.jpg)  
Figure 2: Comparing random input (last two) with the MNIST data (first). Initial eigenvalue profiles are very different, as well as the final profile when compared to figure 1.

# 3 A SIMPLER CASE

In this section, we will repeat the same experiment in two-dimensional data, in an attempt to understand better the connection between the data and the spectrum of the Hessian. We create two Gaussian blobs, centered at  $(1,1)$  and  $(-1, - 1)$ , and first we keep the standard deviation the same, and increase the network size.

![](images/5012d4c692a670c82eea0d4d115c37c9177bc18d304eb2d65d586ea0846a1cef.jpg)  
Figure 3: The input data for the simple case.

The network architecture is the same, two hidden layer fully connected network, with ReLU nonlinearities and a softmax at the top layer combined with a negative log-likelihood loss function. We train the system with gradient descent with constant step size. At the end of the training the norm of the gradient is at the order of  $10^{-4}$ .

![](images/bd0022a2dcba211d53f981a7a3627e1fc06abd084f572f816b0bfe33eef277e7.jpg)  
Figure 4: Increasing the network size: Systems with 18, 74, 162, 282, and 434 parameters, resp.

There are two eigenvalues that are isolated, and away from the bulk of the spectrum. Increasing the network only adds to the concentration of eigenvalues at and around zero. To give an insight into how the Hessian's themselves look like, here we plot the full Hessian matrices for three of the systems above:

![](images/770b473e5289e062bdae64ce87491e1dafae10a1b8c23c74dd9b182e9ecfc021.jpg)  
Rotated Hessian Matrix  
Figure 5: Hessian heatmaps for 18, 74 and 162 paramters systems. The plots are 90 degrees rotates counter-clock wise.

All of the training has been done with random initializations on the weight space with the same standard deviation. In other words, initial points are randomly chosen on the surface of a sphere with a fixed radius given the total number of parameters. This begs the question of the effect of the choice of the initial point. Therefore, now we fix the network size, and repeat the experiment with different random initializations over 5K times, and plot the fluctuations of the top eigenvalue.

![](images/b0fdef03c957744ffd5ce6f98917de789e27a184dc02999e0f88dfcd1e8e9493.jpg)  
Figure 6: Top eigenvalue fluctuations over 5000 runs of the same system with same data and algorithm but different initial points.

The next question is how the spectrum responds to the increased complexity of data. To this end, we keep the architecture the same, and increase the standard deviation of the two Gaussian blobs. They are still centered at the same two points, but it becomes harder to separate them as they merge together. Gradient descent still converges to a low-cost value, but the error is higher, and it can't learn how to separate them perfectly as the blobs merge together. We observe that the top two eigenvalues grow significantly, and beyond its natural fluctuations due to the initialization.

![](images/5253dbc7877655eeec058777cfb5863446af786902753386eec5c41f330781aa.jpg)  
Figure 7: Response of the top eigenvalues to the increased complexity of data. The numbers on top of the figures indicate the standard deviation of the Gaussian blobs. Their means are kept the same at  $(1,1)$  and  $(-1, -1)$ , respectively.

# 4 CONCLUSION

We show that the Hessian of the loss functions in deep learning is degenerate. This has implications on the theoretical work which requires improvements in its premises. One such step has been taken in Panageas & Piliouras (2016) in relaxing the isolated singularity condition that was assumed in Lee et al. (2016). From a practical point of view this has multiple implications:

- The landscape may be flat beyond the notion of wide basins.  
- Training goes to a place with small gradient, but it is not zero.  
- There are still negative eigenvalues even when they are small in magnitude.

This suggests that we may be able to look beyond the classical notions of basins when exploring the energy landscapes of loss functions. Next obvious question is to find low energy paths between solutions to show the kind of flatness in such landscapes. This will be explored in a subsequent work in the same series.

We also demonstrate the two phases of the spectrum, one that is concentrated around zero that depends on the size of the model, and the second part that is away from the bulk of the spectrum, that is isolated and depends on the data.

This kind of two-phased non-degeneracy can, in fact, be a desirable property. A degenerate Hessian implies locally flat regions. A degenerate Hessian at the scale that we observe in deep learning may imply flat regions across space, at the global scale.

- We can devise separate methods for the directions that correspond to the top eigenvalues.  
- We can take advantage of the directions that correspond to the zero or small eigenvalues by attempting to find paths of low energies in the weight space.

As a first step to the last item, initial experiments are promising: Let's take a random point on the weight space and train two systems from that point: (1) with gradient descent, and (2) with stochastic gradient descent. At each step, take a straight line between the two points and interpolate the cost value. The resulting profile is completely flat even when the points keep diverging from one another. Next, take two random initial points on the weight space, so now they are orthogonal to each other. And train two systems with different shuffling of data for SGD. This time one would expect the line interpolation to give arbitrary values since the initial points are completely orthogonal, surprisingly, the line interpolation also decreases albeit not as flat as the previous one.

![](images/3612f0e2493d1acfe6dac0c3cc8556f0fc8dcd1d8a54da2cbf88186e6ec84592.jpg)  
Figure 8:  $z$ -axis is the distance between points. The left most and right most curves in each plot are actual training profiles, and the lines in between are interpolations only. (left figure) same initial point (right figure) random (hence orthogonal) initial points.

![](images/b1422139794056c8cd5c8eb0a7c8df98d3b8ab767e9b3e19e0eea44d71bd7fb1.jpg)

# ACKNOWLEDGMENTS

We would like to thank Afonso Bandeira, Yann Dauphin, Ruoyu Sun, Arthur Szlam and Soumith Chintala for valuable discussions. Part of the research has been conducted when the first author was an intern at FAIR.

# REFERENCES

Moritz Hardt, Benjamin Recht, and Yoram Singer. Train faster, generalize better: Stability of stochastic gradient descent. arXiv preprint arXiv:1509.01240, 2015.  
Yann Lecun, Leon Bottou, Genevieve B Orr, and Klaus-Robert Müller. Efficient backprop. 1998.  
Jason D Lee, Max Simchowitz, Michael I Jordan, and Benjamin Recht. Gradient descent converges to minimizers. University of California, Berkeley, 1050:16, 2016.  
Jiquan Ngiam, Adam Coates, Ahbik Lahiri, Bobby Prochnow, Quoc V Le, and Andrew Y Ng. On optimization methods for deep learning. In Proceedings of the 28th International Conference on Machine Learning (ICML-11), pp. 265-272, 2011.  
Ioannis Panageas and Georgios Piliouras. Gradient descent only converges to minimizers: Non-isolated critical points and invariant regions. arXiv preprint arXiv:1605.00405, 2016.  
Levent Sagun, V Uğur Güney, Gérard Ben Arous, and Yann LeCun. Explorations on high dimensional landscapes. *ICLR 2015 Workshop Contribution*, arXiv:1412.6615, 2014.  
Levent Sagun, Thomas Trogdon, and Yann LeCun. Universality in halting time and its applications in optimization. arXiv preprint arXiv:1511.06444, 2015.