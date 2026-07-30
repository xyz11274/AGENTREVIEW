# NEURAL MACHINE TRANSLATION WITH LATENT SEMANTIC OF IMAGE AND TEXT

Joji Toyama*, Masanori Misono\*†, Masahiro Suzuki, Kotaro Nakayama & Yutaka Matsuo  
Graduate School of Engineering, †Graduate School of Information Science and Technology  
The University of Tokyo  
Hongo, Tokyo, Japan  
{toyama, misono, masa, k-nakayama, matsuo}@weblab.t.u-tokyo.ac.jp

# ABSTRACT

Although attention-based Neural Machine Translation have achieved great success, attention-mechanism cannot capture the entire meaning of the source sentence because the attention mechanism generates a target word depending heavily on the relevant parts of the source sentence. The report of earlier studies has introduced a latent variable to capture the entire meaning of sentence and achieved improvement on attention-based Neural Machine Translation. We follow this approach and we believe that the capturing meaning of sentence benefits from image information because human beings understand the meaning of language not only from textual information but also from perceptual information such as that gained from vision. As described herein, we propose a neural machine translation model that introduces a continuous latent variable containing an underlying semantic extracted from texts and images. Our model, which can be trained end-to-end, requires image information only when training. Experiments conducted with an English-German translation task show that our model outperforms over the baseline.

# 1 INTRODUCTION

Neural machine translation (NMT) has achieved great success in recent years (Sutskever et al., 2014; Bahdanau et al., 2015). In contrast to statistical machine translation, which requires huge phrase and rule tables, NMT requires much less memory. However, the most standard model, NMT with attention (Bahdanau et al., 2015) entails the shortcoming that the attention mechanism cannot capture the entire meaning of a sentence because it generates a target word while depending heavily on the relevant parts of the source sentence (Tu et al., 2016). To overcome this problem, Variational Neural Machine Translation (VNMT), which outperforms NMT with attention introduces a latent variable to capture the underlying semantic from source and target (Zhang et al., 2016). We follow the motivation of VNMT, which is to capture underlying semantic of a source.

Image information is related to language. For example, we human beings understand the meaning of language by linking perceptual information given by the surrounding environment and language (Barsalou, 1999). Although it is natural and easy for humans, it is difficult for computers to understand different domain's information integrally. Solving this difficult task might, however, bring great improvements in natural language processing. Several researchers have attempted to link language and images such as image captioning by Xu et al. (2015) or image generation from sentences by Reed et al. (2016). They described the possibility of integral understanding of images and text. In machine translation, we can expect an improvement using not only text information but also image information because image information can bridge two languages.

As described herein, we propose the neural machine translation model which introduces a latent variable containing an underlying semantic extracted from texts and images. Our model includes an explicit latent variable  $\mathbf{z}$ , which has underlying semantics extracted from text and images by introducing a Variational Autoencoder (VAE) (Kingma et al., 2014; Rezende et al., 2014). Our model,

![](images/d4eab91aa80cf3b564117522589ad547af57deb1523e5558f49a400c099ad11b.jpg)  
Figure 1: Architecture of Proposed Model. Green dotted lines denote that  $\pi$  and encoded  $\mathbf{y}$  are used only when training.

which can be trained end-to-end, requires image information only when training. As described herein, we tackle the task with which one uses a parallel corpus and images in training, while using a source corpus in translating. It is important to define the task in this manner because we rarely have a corresponding image when we want to translate a sentence. During translation, our model generates a semantic variable  $\mathbf{z}$  from a source, integrates variable  $\mathbf{z}$  into a decoder of neural machine translation system, and then finally generates the translation. The difference between our model and VNMT is that we use image information in addition to text information.

For experiments, we used Multi30k (Elliott et al., 2016), which includes images and the corresponding parallel corpora of English and German. Our model outperforms the baseline with two evaluation metrics: METEOR (Denkowski & Lavie, 2014) and BLEU (Papineni et al., 2002). Moreover, we obtain some knowledge related to our model and Multi30k. Finally, we present some examples in which our model either improved, or worsened, the result.

Our paper contributes to the neural machine translation research community in three ways.

- We present the first neural machine translation model to introduce a latent variable inferred from image and text information. We also present the first translation task with which one uses a parallel corpus and images in training, while using a source corpus in translating.  
- Our translation model can generate more accurate translation by training with images, especially for short sentences.  
- We present how the translation of source is changed by adding image information compared to VNMT which does not use image information.

# 2 BACKGROUND

Our model is the extension of Variational Neural Machine Translation (VNMT) (Zhang et al., 2016). Our model is also viewed as one of the multimodal translation models. In our model, VAE is used to introduce a latent variable. We describe the background of our model in this section.

# 2.1 VARIATIONAL NEURAL MACHINE TRANSLATION

The VNMT translation model introduces a latent variable. This model's architecture shown in Figure 1 excludes the arrow from  $\pi$ . This model involves three parts: encoder, inferrer, and decoder. In the encoder, both the source and target are encoded by bidirectional-Recurrent Neural Networks (bidirectional-RNN) and a semantic representation is generated. In the inferrer, a latent variable  $\mathbf{z}$  is

modeled from a semantic representation by introducing VAE. In the decoder, a latent variable  $\mathbf{z}$  is integrated in the Gated Recurrent Unit (GRU) decoder; also, a translation is generated.

Our model is followed by architecture, except that the image is also encoded to obtain a latent variable  $\mathbf{z}$ .

# 2.2 MULTIMODAL TRANSLATION

Multimodal Translation is the task with which one might one can use a parallel corpus and images. The first papers to study multimodal translation are Elliott et al. (2015) and Hitschler & Riezler (2016). It was selected as a shared task in Workshop of Machine Translation 2016 (WMT16<sup>1</sup>). Although several studies have been conducted (Caglayan et al., 2016; Huang et al., 2016; Calixto et al., 2016; Libovický et al., 2016; Rodríguez Guasch & Costa-jussà, 2016; Shah et al., 2016), they do not show great improvement, especially in neural machine translation (Specia et al., 2016). Here, we introduce end-to-end neural network translation models like our model.

Caglayan et al. (2016) integrate an image into an NMT decoder. They simply put source context vectors and image feature vectors extracted from ResNet-50's 'res4f_relu' layer (He et al., 2016) into the decoder called multimodal conditional GRU. They demonstrate that their method does not surpass the text-only baseline: NMT with attention.

Huang et al. (2016) integrate an image into a head of source words sequence. They extract prominent objects from the image by Region-based Convolutional Neural Networks (R-CNN) (Girshick, 2015). Objects are then converted to feature vectors by VGG-19 (Simonyan & Zisserman, 2014) and are put into a head of source words sequence. They demonstrate that object extraction by R-CNN contributes greatly to the improvement. This model achieved the highest METEOR score in NMT-based models in WMT16, which we compare to our model in the experiment. We designate this model as CMU.

Caglayan et al. (2016) argue that their proposed model did not achieve improvement because they failed to benefit from both text and images. We assume that they failed to integrate text and images because they simply put images and text into neural machine translation despite huge gap exists between image information and text information. Our model, however, presents the possibility of benefitting from images and text because text and images are projected to their common semantic space so that the gap of images and text would be filled.

# 2.3 VARIATIONAL AUTO ENCODER

VAE was proposed in an earlier report of the literature Kingma et al. (2014); Rezende et al. (2014). Given an observed variable  $\mathbf{x}$ , VAE introduces a continuous latent variable  $\mathbf{z}$ , with the assumption that  $\mathbf{x}$  is generated from  $\mathbf{z}$ . VAE incorporates  $p_{\theta}(\mathbf{x}|\mathbf{z})$  and  $q_{\phi}(\mathbf{z}|\mathbf{x})$  into an end-to-end neural network. The lower bound is shown below.

$$
\mathcal {L} _ {\mathrm {V A E}} = - \mathrm {D} _ {\mathrm {K L}} \left[ q _ {\phi} (\mathbf {z} | \mathbf {x}) \left| \left| p _ {\theta} (\mathbf {z}) \right| \right. \right] + \mathbb {E} _ {q _ {\phi} (\mathbf {z} | \mathbf {x})} \left[ \log p _ {\theta} (\mathbf {x} | \mathbf {z}) \right] \leq \log p _ {\theta} (\mathbf {x}) \tag {1}
$$

# 3 NEURAL MACHINE TRANSLATION WITH LATENT SEMANTIC OF IMAGE AND TEXT

We propose a neural machine translation model which explicitly has a latent variable containing an underlying semantic extracted from both text and image. This model can be seen as an extension of VNMT by adding image information.

Our model can be drawn as a graphical model in Figure 3. Its lower bound is

$$
\mathcal {L} = - \mathrm {D} _ {\mathrm {K L}} \left[ q _ {\phi} (\mathbf {z} | \mathbf {x}, \mathbf {y}, \boldsymbol {\pi}) | | p _ {\theta} (\mathbf {z} | \mathbf {x}) \right] + \mathbb {E} _ {q _ {\phi} (\mathbf {z} | \mathbf {x}, \mathbf {y}, \boldsymbol {\pi})} \left[ \log p _ {\theta} (\mathbf {y} | \mathbf {z}, \mathbf {x}) \right], \tag {2}
$$

where  $\mathbf{x},\mathbf{y},\pi ,\mathbf{z}$  respectively denote the source, target, image and latent variable, and  $p_{\theta}$  and  $q_{\phi}$  respectively denote the prior distribution and the approximate posterior distribution. It is noteworthy in Eq. (2) that we want to model  $p(\mathbf{z}|\mathbf{x},\mathbf{y},\pi)$ , which is intractable. Therefore we model  $q_{\phi}(\mathbf{z}|\mathbf{x},\mathbf{y},\pi)$

![](images/d4d72b08376a95e3dbe6d145a08598b817a5c4de7b22f99e9ce11ccee82a8483.jpg)  
Figure 2: VNMT  
Figure 3: Our model

instead, and also model prior  $p_{\theta}(\mathbf{z}|\mathbf{x})$  so that we can generate a translation from the source in testing. Derivation of the formula is presented in the appendix.

We model all distributions in Eq. (2) by neural networks. Our model architecture is divisible into three parts: 1) encoder, 2) inferrer, and 3) decoder.

# 3.1 ENCODER

In the encoder, the semantic representation  $\mathbf{h}_{\mathrm{e}}$  is obtained from the image, source, and target. We propose several methods to encode an image. We show how these methods affect the translation result in the Experiment section. This representation is used in the inferrer. This section links to the green part of Figure 1.

# 3.1.1 TEXTENCODING

The source and target are encoded in the same way as Bahdanau et al. (2015). The source is converted to a sequence of 1-of-k vector and is embedded to  $d_{emb}$  dimensions. We designate it as the source sequence. Then, a source sequence is put into bidirectional RNN. Representation  $\mathbf{h}_i$  is obtained by concatenating  $\vec{\mathbf{h}}_i$  and  $\vec{\mathbf{h}}_i: \vec{\mathbf{h}}_i = \mathrm{RNN}(\vec{\mathbf{h}}_{i-1}, E_{w_i})$ ,  $\vec{\mathbf{h}}_i = \mathrm{RNN}(\vec{\mathbf{h}}_{i+1}, E_{w_i})$ ,  $\mathbf{h}_i = [\vec{\mathbf{h}}_i; \vec{\mathbf{h}}_i]$ , where  $E_{w_i}$  is the embedded word in a source sentence,  $\mathbf{h}_i \in \mathbb{R}^{d_h}$ , and  $\vec{\mathbf{h}}_i, \vec{\mathbf{h}}_i \in \mathbb{R}^{\frac{d_h}{2}}$ . It is conducted through  $i = 0$  to  $i = T_f$ , where  $T_f$  is the sequence length. GRU is implemented in bidirectional RNN so that it can attain long-term dependence. Finally, we conduct mean-pooling to  $\mathbf{h}_i$  and obtain the source representation vector as  $\mathbf{h}_f = \frac{1}{T_f} \sum_i^{T_f} \mathbf{h}_i$ . The exact same process is applied to target to obtain target representation  $\mathbf{h}_g$ .

# 3.1.2 IMAGE ENCODING AND SEMANTIC REPRESENTATION

We use Convolutional Neural Networks (CNN) to extract feature vectors from images. We propose several ways of extracting image features.

Global (G) The image feature vector is extracted from the image using a CNN. With this method, we use a feature vector in the certain layer as  $\pi$ . Then  $\pi$  is encoded to the image representation vector  $\mathbf{h}_{\pi}$  simply by affine transformation as

$$
\mathbf {h} _ {\pi} = W _ {\pi} \boldsymbol {\pi} + b _ {\pi} \quad \text {w h e r e} W _ {\pi} \in \mathbb {R} ^ {d _ {\pi} \times d _ {f c 7}}, b _ {\pi} \in \mathbb {R} ^ {d _ {\pi}}. \tag {3}
$$

Global and Objects (G+O) First we extract some prominent objects from images in some way. Then, we obtain fc7 image feature vectors  $\pi$  from the original image and extracted objects using a CNN. Therefore  $\pi$  takes a variable length. We handle  $\pi$  in two ways: average and RNN encoder.

In average  $(\mathbf{G} + \mathbf{O} - \mathbf{AVG})$ , we first obtain intermediate image representation vector  $\mathbf{h}_{\pi}'$  by affine transformation in Eq. (3). Then, the average of  $\mathbf{h}_{\pi}'$  becomes the image representation vector:  $\mathbf{h}_{\pi} = \frac{\sum_{i}^{l}\mathbf{h}_{\pi_{i}}^{\prime}}{l}$ , where  $l$  is the length of  $\mathbf{h}_{\pi}'$ .

In RNN encoder  $(\mathbf{G} + \mathbf{O}$  -RNN), we first obtain  $\mathbf{h}_{\pi}^{\prime}$  by affine transformation in Eq. (3). Then, we encode  $\mathbf{h}_{\pi}^{\prime}$  in the same way as we encode text in Section 3.1.1 to obtain  $\mathbf{h}_{\pi}$

Global and Objects into source and target (G+O-TXT) Thereby, we first obtain  $\mathbf{h}_{\pi}^{\prime}$  by affine transformation in Eq. (3). Then, we put sequential vector  $\mathbf{h}_{\pi}^{\prime}$  into the head of the source sequence and target sequence. In this case, we set  $d_{\pi}$  to be the same dimension as  $d_{emb}$ . In fact, the source sequence including  $\mathbf{h}_{\pi}^{\prime}$  is only used to model  $q_{\phi}(\mathbf{z}|\mathbf{x},\mathbf{y},\boldsymbol{\pi})$ . Context vector  $\mathbf{c}$  (Eq. (15)) and  $p_{\theta}(\mathbf{z}|\mathbf{x})$  are computed by a source sequence that does not include  $\mathbf{h}_{\pi}^{\prime}$ . We encode the source sequence including  $\mathbf{h}_{\pi}^{\prime}$  as Section 3.1.1 to obtain  $\mathbf{h}_f$  and  $\mathbf{h}_g$ . In this case,  $\mathbf{h}_{\pi}$  is not obtained. Image information is contained in  $\mathbf{h}_f$  and  $\mathbf{h}_g$ .

All representation vectors  $\mathbf{h}_f$ ,  $\mathbf{h}_g$  and  $\mathbf{h}_{\pi}$  are concatenated to obtain a semantic representation vector as  $\mathbf{h}_e = [\mathbf{h}_f;\mathbf{h}_g;\mathbf{h}_{\pi}]$ , where  $\mathbf{h}_e \in \mathbb{R}^{d_e = 2\times d_h + d_\pi}$  (in G+O-TXT:  $\mathbf{h}_e = [\mathbf{h}_f;\mathbf{h}_g]$ , where  $\mathbf{h}_e \in \mathbb{R}^{d_e = 2\times d_h}$ ). It is an input of the multimodal variational neural inferrer.

# 3.2 INFERRER

We model the posterior  $q_{\phi}(\mathbf{z}|\mathbf{x},\mathbf{y},\boldsymbol{\pi})$  using a neural network and also the prior  $p_{\theta}(\mathbf{z}|\mathbf{x})$  by neural network. This section links to the black and grey part of Figure 1.

# 3.2.1 NEURAL POSTERIOR APPROXIMATOR

Modeling the true posterior  $p_{\theta}(\mathbf{z}|\mathbf{x},\mathbf{y},\pmb{\pi})$  is usually intractable. Therefore, we consider modeling of an approximate posterior  $q_{\phi}(\mathbf{z}|\mathbf{x},\mathbf{y},\pmb{\pi})$  by introducing VAE. We assume that the posterior  $q_{\phi}(\mathbf{z}|\mathbf{x},\mathbf{y},\pmb{\pi})$  has the following form:

$$
q _ {\phi} (\mathbf {z} | \mathbf {x}, \mathbf {y}, \pi) = \mathcal {N} (\mathbf {z}; \boldsymbol {\mu} (\mathbf {x}, \mathbf {y}, \pi), \sigma (\mathbf {x}, \mathbf {y}, \pi) ^ {2} \mathbf {I}). \tag {4}
$$

The mean  $\mu$  and standard deviation  $\sigma$  of the approximate posterior are the outputs of neural networks.

Starting from the variational neural encoder, a semantic representation vector  $\mathbf{h}_e$  is projected to latent semantic space as

$$
\mathbf {h} _ {z} = g \left(W _ {z} ^ {(1)} \mathbf {h} _ {e} + \mathbf {b} _ {z} ^ {(1)}\right), \tag {5}
$$

where  $W_{z}^{(1)} \in \mathbb{R}^{d_{z} \times (d_{e})}$ $\mathbf{b}_{z}^{(1)} \in \mathbb{R}^{d_{z}}$ .  $g(\cdot)$  is an element-wise activation function, which we set as  $\tanh(\cdot)$ . Gaussian parameters of Eq. (4) are obtained through linear regression as

$$
\boldsymbol {\mu} = W _ {\mu} \mathbf {h} _ {z} + \mathbf {b} _ {\mu}, \log \sigma^ {2} = W _ {\sigma} \mathbf {h} _ {z} + \mathbf {b} _ {\sigma}, \tag {6}
$$

where  $\pmb{\mu},\log \pmb{\sigma}^2\in \mathbb{R}^{d_z}$

# 3.2.2 NEURAL PRIOR MODEL

We model the prior distribution  $p_{\theta}(\mathbf{z}|\mathbf{x})$  as follows:

$$
p _ {\theta} (\mathbf {z} | \mathbf {x}) = \mathcal {N} (\mathbf {z}; \boldsymbol {\mu} ^ {\prime} (\mathbf {x}), \boldsymbol {\sigma} ^ {\prime} (\mathbf {x}) ^ {2} \mathbf {I}). \tag {7}
$$

$\pmb{\mu}^{\prime}$  and  $\sigma^\prime$  are generated in the same way as that presented in Section 3.2.1, except for the absence of  $\mathbf{y}$  and  $\pi$  as inputs. Because of the absence of representation vectors, the dimensions of weight in equation (5) for prior model are  $W_{z}^{\prime(1)} \in \mathbb{R}^{d_{z} \times d_{h}}$ ,  $\mathbf{b}_{z}^{\prime(1)} \in \mathbb{R}^{d_{z}}$ . We use a reparameterization trick to obtain a representation of latent variable  $\mathbf{z}$ :  $\mathbf{h}_{z}^{\prime} = \pmb{\mu} + \pmb{\sigma}\pmb{\epsilon}$ ,  $\pmb{\epsilon} \sim \mathcal{N}(0, I)$ . During translation,  $\mathbf{h}_{z}^{\prime}$  is set as the mean of  $p_{\theta}(\mathbf{z}|\mathbf{x})$ . Then,  $\mathbf{h}_{z}^{\prime}$  is projected onto the target space as

$$
\mathbf {h} _ {e} ^ {\prime} = g \left(W _ {z} ^ {(2)} \mathbf {h} _ {z} ^ {\prime} + \mathbf {b} _ {z} ^ {(2)}\right) \quad \text {w h e r e} \mathbf {h} _ {e} ^ {\prime} \in \mathbb {R} ^ {d _ {e}}. \tag {8}
$$

$\mathbf{h}_e^{\prime}$  is then integrated into the neural machine translation's decoder.

# 3.3 DECODER

This section links to the orange part of Figure 1. Given the source sentence  $\mathbf{x}$  and the latent variable  $\mathbf{z}$ , decoder defines the probability over translation  $\mathbf{y}$  as

$$
p (\mathbf {y} | \mathbf {z}, \mathbf {x}) = \prod_ {j = 1} ^ {T} p \left(\mathbf {y} _ {j} \mid \mathbf {y} _ {<   j}, \mathbf {z}, \mathbf {x}\right). \tag {9}
$$

How we define the probability over translation  $\mathbf{y}$  is fundamentally the same as VNMT, except for using conditional GRU instead of GRU. Conditional GRU involves two GRUs and an attention mechanism. We integrate a latent variable  $\mathbf{z}$  into the second GRU. We describe it in the appendix.

# 3.4 MODEL TRAINING

Monte Carlo sampling method is used to approximate the expectation over the posterior Eq. (2),  $\mathbb{E}_{q_{\phi}(\mathbf{z}|\mathbf{x},\mathbf{y},\pi)}\approx \frac{1}{L}\sum_{l = 1}^{L}\log p_{\theta}(\mathbf{y}|\mathbf{x},\mathbf{h}_z^{(l)})$  , where  $L$  is the number of samplings. The training objective is defined as

$$
\mathcal {L} (\theta , \phi) = - \mathrm {D} _ {\mathrm {K L}} \left[ q _ {\phi} (\mathbf {z} | \mathbf {x}, \mathbf {y}, \boldsymbol {\pi}) | | p _ {\theta} (\mathbf {z} | \mathbf {x}) \right] + \frac {1}{L} \sum_ {l = 1} ^ {L} \sum_ {j = 1} ^ {T} \log p _ {\theta} \left(\mathbf {y} _ {j} | \mathbf {y} _ {<   j}, \mathbf {x}, \mathbf {h} _ {z} ^ {(l)}\right), \tag {10}
$$

where  $\mathbf{h}_z = \boldsymbol{\mu} + \boldsymbol{\sigma} \cdot \boldsymbol{\epsilon}, \boldsymbol{\epsilon} \sim \mathcal{N}(0, I)$ . The first term, KL divergence, can be computed analytically and is differentiable because both distributions are Gaussian. The second term is also differentiable. We set  $L$  as 1. Overall, the objective  $\mathcal{L}$  is differentiable. Therefore, we can optimize the parameter  $\theta$  and variational parameter  $\phi$  using gradient ascent techniques.

# 4 EXPERIMENTS

# 4.1 EXPERIMENTAL SETUP

We used Multi30k (Elliott et al., 2016) as the dataset. Multi30k have an English description and a German description for each corresponding image. We handle 29,000 pairs as training data, 1,014 pairs as validation data, and 1,000 pairs as test data.

Before training, punctuation normalization and lowercase are applied to both English and German sentences by Moses (Koehn et al., 2007) scripts<sup>2</sup>. Compound-word splitting is conducted only to German sentences using Sennrich et al. (2016)<sup>3</sup>. Then we tokenize sentences<sup>2</sup> and use them as training data. We produce vocabulary dictionaries from training data. The vocabulary becomes 10,211 words for English and 13,180 words for German after compound-word splitting.

Image features are extracted using VGG-19 CNN (Simonyan & Zisserman, 2014). We use 4096-dimensional fc7 features. To extract the object's region, we use Fast R-CNN (Girshick, 2015). Fast R-CNN is trained on ImageNet and MSCOCO dataset<sup>4</sup>.

All weights are initialized by  $\mathcal{N}(0,0.01\mathbf{I})$ . We use the adadelta algorithm as an optimization method. The hyperparameters used in the experiment are presented in the Appendix. All models are trained with early stopping. When training, VNMT is fine-tuned by NMT model and our models are fine-tuned using VNMT. When translating, we use beam-search. The beam-size is set as 12. Before evaluation, we restore split words to the original state and de-tokenize<sup>2</sup> generated sentences.

We implemented proposed models based on  $dl4mt^5$ . Actually,  $dl4mt$  is fundamentally the same model as Bahdanau et al. (2015), except that its decoder employs conditional GRU<sup>6</sup>. We implemented VNMT also with conditional GRU so small difference exists between our implementation

and originally proposed VNMT which employs normal GRU as a decoder. We evaluated results based on METEOR and BLUE using MultEval<sup>7</sup>.

# 4.2 RESULT

Table 1 presents experiment results. It shows that our models outperforms the baseline in both METEOR and BLEU. Figure 4 shows the plot of METEOR score of baselines and our models models in validation. Figure 5 shows the plot of METEOR score and the source sentence length.

Table 1: Evaluation Result on Multi30k dataset (English-German). The scores in parentheses are computed with 'norm' parameter. NMT is dl4mt's NMT (in the session3 directory). The score of the CMU is from (Huang et al., 2016).  

<table><tr><td rowspan="2" colspan="2"></td><td colspan="2">METEOR ↑</td><td colspan="2">BLEU ↑</td></tr><tr><td>val</td><td>test</td><td>val</td><td>test</td></tr><tr><td rowspan="7">Our Model</td><td>NMT</td><td>51.5 (55.8)</td><td>50.5 (54.9)</td><td>35.8</td><td>33.1</td></tr><tr><td>VNMT</td><td>52.2 (56.3)</td><td>51.1 (55.3)</td><td>37.0</td><td>34.9</td></tr><tr><td>CMU</td><td>- (-)</td><td>- (54.1)</td><td>-</td><td>-</td></tr><tr><td>G</td><td>50.6 (54.8)</td><td>52.4 (56.0)</td><td>34.5</td><td>36.5</td></tr><tr><td>G+O-AVG</td><td>51.8 (55.8)</td><td>51.8 (55.8)</td><td>35.7</td><td>35.8</td></tr><tr><td>G+O-RNN</td><td>51.8 (56.1)</td><td>51.0 (55.4)</td><td>35.9</td><td>34.9</td></tr><tr><td>G+O-TXT</td><td>52.6 (56.8)</td><td>51.7 (56.0)</td><td>36.6</td><td>35.1</td></tr></table>

# 4.3 QUANTITATIVE ANALYSIS

Table 1 shows that G scores the best in proposed models. In G, we simply put the feature of the original image. Actually, proposed model does not benefit from R-CNN, presumably because we can not handle sequences of image features very well. For example,  $\mathrm{G + O - AVG}$  uses the average of multiple image features, but it only makes the original image information unnecessarily confusing.

Figure 4 shows that G and  $\mathrm{G + O - AVG}$  outperforms VNMT almost every time, but all model scores increase suddenly in the 17,000 iteration validation. We have no explanation for this behavior. Figure 4 also shows that G and  $\mathrm{G + O - AVG}$  scores fluctuate more moderately than others. We state that G and  $\mathrm{G + O - AVG}$  gain stability by adding image information. When one observes the difference between the test score and the validation score for each model, baseline scores decrease more than proposed model scores. Especially, the G score increases in the test, simply because proposed models produce a better METEOR score on average, as shown in Figure 4.

Figure 5 shows that G and  $\mathrm{G + O - AVG}$  make more improvements on baselines in short sentences than in long sentences, presumably because  $q_{\phi}(\mathbf{z}|\mathbf{x},\mathbf{y},\pi)$  can model  $\mathbf{z}$  well when a sentence is short. Image features always have the same dimension, but underlying semantics of the image and text differ. We infer that when the sentence is short, image feature representation can afford to approximate the underlying semantic, but when a sentence is long, image feature representation can not approximate the underlying semantic.

Multi30k easily becomes overfitted, as shown in Figure 8 and 9 in the appendix. This is presumably because 1) Multi30k is the descriptions of image, making the sentences short and simple, and 2) Multi30k has 29,000 sentences, which could be insufficient. In the appendix, we show how the parameter setting affects the score. One can see that decay-c has a strong effect. Huang et al. (2016) states that their proposed model outperforms the baseline (NMT), but we do not have that observation. It can be assumed that their baseline parameters are not well tuned.

# 4.4 QUALITATIVE ANALYSIS

We presented the top 30 sentences, which make the largest METEOR score difference between G and VNMT, to native speakers of German and get the overall comments. They were not informed of

![](images/616e043c67eb05bc2f2c477df086404e356d0817fb7f3f14514ebe5b434be009.jpg)  
Figure 4: METEOR score to the validation data which are calculated for each 1000 iterations.

![](images/4f983bf4078d68532bca7fe5c7edb16caa32be0a81a356e1e995c0b8c2bd644c.jpg)  
Figure 5: METEOR score on different groups of the source sentence length.

our model training with image in addition to text. These comments are summarized into two general remarks. One is that G translates the meaning of the source material more accurately than VNMT. The other is that our model has more grammatical errors as prepositions' mistakes or missing verbs compared to VNMT. We assume these two remarks are reasonable because G is trained with images which mainly have a representation of noun rather than verb, therefore can capture the meaning of materials in sentence.

Figure 6 presents the translation results and the corresponding image which G translates more accurately than VNMT in METEOR. Figure 7 presents the translation results and the corresponding image which G translates less accurately than VNMT in METEOR. Again, we note that our model does not use image during translating. In Figure 6, G translates "a white and black dog" correctly while VNMT translates it incorrectly implying "a white dog and a black dog". We assume that G correctly translates the source because G captures the meaning of material in the source. In Figure 7, G incorrectly translates the source. Its translation result is missing the preposition meaning "at", which is hardly represented in image. We present more translation examples in appendix.

![](images/241489a003117a8f5868abfa0c072212d1c846fa080c5fc3eff92da02510f492.jpg)

<table><tr><td>Source</td><td>a woman holding a white and black dog.</td></tr><tr><td>Truth</td><td>eine Frau hält einen weiß-schwarzen Hund.</td></tr><tr><td>VNMT</td><td>eine Frau hält einen weißen und schwarzen Hund.</td></tr><tr><td>Our Model (G)</td><td>eine Frau hält einen weiß-schwarzen Hund.</td></tr></table>

Figure 6: Translation 1

![](images/60c90cfeb00529556da115c79e65a26396b00dc67862ccbdc4ea11a63b2abc85.jpg)  
Figure 7: Translation 2

<table><tr><td>Source</td><td>a group of people running a marathon in the winter.</td></tr><tr><td>Truth</td><td>eine gruppe von menschen lauft bei einem marathon im winter.</td></tr><tr><td>VNMT</td><td>eine gruppe von menschen lauft bei einem marathon im winter.</td></tr><tr><td>Our Model (G)</td><td>eine gruppe leute lauft einen marathon im winter an.</td></tr></table>

# 5 CONCLUSION

As described herein, we proposed the neural machine translation model that explicitly has a latent variable that includes underlying semantics extracted from both text and images. Our model outperforms the baseline in both METEOR and BLEU scores. Experiments and analysis present that our model can generate more accurate translation for short sentences. In qualitative analysis, we present that our model can translate nouns accurately while our model makes grammatical errors.

# REFERENCES

Dzmitry Bahdanau, Kyunghyun Cho, and Yoshua Bengio. Neural machine translation by jointly learning to align and translate. In ICLR, 2015.  
Lawrence W. Barsalou. Perceptual symbol Systems. Behavioral and Brain Sciences, 22:577-609, 1999.  
Ozan Caglayan, Walid Aransa, Yaxing Wang, Marc Masana, Mercedes Garcia-Martinez, Fethi Bougares, Loic Barrault, and Joost van de Weijer. Does Multimodality Help Human and Machine for Translation and Image Captioning? In WMT, 2016.  
Iacer Calixto, Desmond Elliott, and Stella Frank. DCU-UvA Multimodal MT System Report. In Proceedings of the First Conference on Machine Translation, pp. 634-638. Association for Computational Linguistics, 2016.  
Michael Denkowski and Alon Lavie. Meteor Universal: Language Specific Translation Evaluation for Any Target Language. In Proceedings of the EACL 2014 Workshop on Statistical Machine Translation, 2014.  
D. Elliott, S. Frank, and E. Hasler. Multilingual Image Description with Neural Sequence Models. ArXiv e-prints, 2015.  
Desmond Elliott, Stella Frank, Khalil Sima'an, and Lucia Specia. Multi30K: Multilingual English-German Image Descriptions. CoRR, abs/1605.00459, 2016.  
Ross Girshick. Fast R-CNN. In ICCV, 2015.  
Kaiming He, Xiangyu Zhang, Shaoqing Ren, and Jian Sun. Deep Residual Learning for Image Recognition. In CVPR, 2016.

Julian Hitschler and Stefan Riezler. Multimodal Pivots for Image Caption Translation. arXiv preprint arXiv:1601.03916, 2016.  
Po-Yao Huang, Frederick Liu, Sz-Rung Shiang, Jean Oh, and Chris Dyer. Attention-based Multi-modal Neural Machine Translation. In WMT, 2016.  
Diederik P Kingma, Shakir Mohamed, Danilo Jimenez Rezende, and Max Welling. Semi-supervised Learning with Deep Generative Models. In NIPS, 2014.  
Philipp Koehn, Hieu Hoang, Alexandra Birch, Chris Callison-Burch, Marcello Federico, Nicola Bertoldi, Brooke Cowan, Wade Shen, Christine Moran, Richard Zens, Chris Dyer, Ondrej Bojar, Alexandra Constantin, and Evan Herbst. Moses: Open Source Toolkit for Statistical Machine Translation. In ACL, 2007.  
Jindrich Libovický, Jindrich Helcl, Marek Tlusty, Ondrej Bojar, and Pavel Pecina. CUNI System for WMT16 Automatic Post-Editing and Multimodal Translation Tasks. In Proceedings of the First Conference on Machine Translation, pp. 646-654. Association for Computational Linguistics, 2016.  
Kishore Papineni, Salim Roukos, Todd Ward, and Wei-Jing Zhu. BLEU: A Method for Automatic Evaluation of Machine Translation. In ACL, 2002.  
Scott Reed, Zeynep Akata, Xinchen Yan, Lajanugen Logeswaran, Bernt Schiele, and Honglak Lee. Generative Adversarial Text to Image Synthesis. In ICML, 2016.  
Danilo J. Rezende, Shakir Mohamed, and Daan Wierstra. Stochastic Backpropagation and Approximate Inference in Deep Generative Models. In ICML, 2014.  
Sergio Rodríguez Guasch and Marta R. Costa-jussà. WMT 2016 Multimodal Translation System Description based on Bidirectional Recurrent Neural Networks with Double-Embeddings. In Proceedings of the First Conference on Machine Translation, pp. 655–659. Association for Computational Linguistics, 2016.  
Rico Sennrich, Barry Haddow, and Alexandra Birch. Neural Machine Translation of Rare Words with Subword Units. In ACL, 2016.  
Kashif Shah, Josiah Wang, and Lucia Specia. SHEF-Multimodal: Grounding Machine Translation on Images. In Proceedings of the First Conference on Machine Translation, pp. 660-665. Association for Computational Linguistics, 2016.  
Karen Simonyan and Andrew Zisserman. Very Deep Convolutional Networks for Large-Scale Image Recognition. CoRR, abs/1409.1556, 2014.  
Lucia Specia, Stella Frank, Khalil Sima' an, and Desmond Elliott. A shared Task on Multimodal Machine Translation and Crosslingual Image Description. In Proceedings of the First Conference on Machine Translation, Berlin, Germany. Association for Computational Linguistics, 2016.  
Ilya Sutskever, Oriol Vinyals, and Quoc V Le. Sequence to Sequence Learning with Neural Networks. In NIPS, 2014.  
Zhaopeng Tu, Zhengdong Lu, Yang Liu, Xiaohua Liu, and Hang Li. Modeling Coverage for Neural Machine Translation. In ACL, 2016.  
Kelvin Xu, Jimmy Ba, Ryan Kiros, Kyunghyun Cho, Aaron Courville, Ruslan Salakhutdinov, Richard S Zemel, and Yoshua Bengio. Show, Attend and Tell: Neural Image Caption Generation with Visual Attention. In CVPR, 2015.  
Biao Zhang, Deyi Xiong, and Jinsong Su. Variational Neural Machine Translation. In EMNLP, 2016.
