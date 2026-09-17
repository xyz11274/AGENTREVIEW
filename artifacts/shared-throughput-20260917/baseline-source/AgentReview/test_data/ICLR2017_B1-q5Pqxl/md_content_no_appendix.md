# MACHINE COMPREHENSION USING MATCH-LSTM AND ANSWER POINTER

Shuohang Wang

School of Information Systems

Singapore Management University

shwang.2014@phdis.smu.edu.sg

Jing Jiang

School of Information Systems

Singapore Management University

jingjiang@smu.edu.sg

# ABSTRACT

Machine comprehension of text is an important problem in natural language processing. A recently released dataset, the Stanford Question Answering Dataset (SQuAD), offers a large number of real questions and their answers created by humans through crowdsourcing. SQuAD provides a challenging testbed for evaluating machine comprehension algorithms, partly because compared with previous datasets, in SQuAD the answers do not come from a small set of candidate answers and they have variable lengths. We propose an end-to-end neural architecture for the task. The architecture is based on match-LSTM, a model we proposed previously for textual entailment, and Pointer Net, a sequence-to-sequence model proposed by Vinyals et al. (2015) to constrain the output tokens to be from the input sequences. We propose two ways of using Pointer Net for our task. Our experiments show that both of our two models substantially outperform the best results obtained by Rajpurkar et al. (2016) using logistic regression and manually crafted features.

# 1 INTRODUCTION

Machine comprehension of text is one of the ultimate goals of natural language processing. While the ability of a machine to understand text can be assessed in many different ways, in recent years, several benchmark datasets have been created to focus on answering questions as a way to evaluate machine comprehension (Richardson et al., 2013; Hermann et al., 2015; Hill et al., 2016; Weston et al., 2016; Rajpurkar et al., 2016). In this setup, typically the machine is first presented with a piece of text such as a news article or a story. The machine is then expected to answer one or multiple questions related to the text.

In most of the benchmark datasets, a question can be treated as a multiple choice question, whose correct answer is to be chosen from a set of provided candidate answers (Richardson et al., 2013; Hill et al., 2016). Presumably, questions with more given candidate answers are more challenging. The Stanford Question Answering Dataset (SQuAD) introduced recently by Rajpurkar et al. (2016) contains such more challenging questions whose correct answers can be any sequence of tokens from the given text. Moreover, unlike some other datasets whose questions and answers were created automatically in Cloze style (Hermann et al., 2015; Hill et al., 2016), the questions and answers in SQuAD were created by humans through crowdsourcing, which makes the dataset more realistic. Given these advantages of the SQuAD dataset, in this paper, we focus on this new dataset to study machine comprehension of text. A sample piece of text and three of its associated questions are shown in Table 1.

Traditional solutions to this kind of question answering tasks rely on NLP pipelines that involve multiple steps of linguistic analyses and feature engineering, including syntactic parsing, named entity recognition, question classification, semantic parsing, etc. Recently, with the advances of applying neural network models in NLP, there has been much interest in building end-to-end neural architectures for various NLP tasks, including several pieces of work on machine comprehension (Hermann et al., 2015; Hill et al., 2016; Yin et al., 2016; Kadlec et al., 2016; Cui et al., 2016). However, given the properties of previous machine comprehension datasets, existing end-to-end neural architectures for the task either rely on the candidate answers (Hill et al., 2016; Yin et al., 2016) or assume that the

In 1870, Tesla moved to Karlovac, to attend school at the Higher Real Gymnasium, where he was profoundly influenced by a math teacher Martin Sekulic. The classes were held in German, as it was a school within the Austro-Hungarian Military Frontier. Tesla was able to perform integral calculus in his head, which prompted his teachers to believe that he was cheating. He finished a four-year term in three years, graduating in 1873.

1. In what language were the classes given?

German

2. Who was Tesla's main influence in Karlovac?

Martin Sekulic

3. Why did Tesla go to Karlovac?

attend school at the Higher Real Gymnasium

Table 1: A paragraph from Wikipedia and three associated questions together with their answers, taken from the SQuAD dataset. The tokens in bold in the paragraph are our predicted answers while the texts next to the questions are the ground truth answers.

answer is a single token (Hermann et al., 2015; Kadlec et al., 2016; Cui et al., 2016), which make these methods unsuitable for the SQuAD dataset. In this paper, we propose a new end-to-end neural architecture to address the machine comprehension problem as defined in the SQuAD dataset.

Specifically, observing that in the SQuAD dataset many questions are paraphrases of sentences from the original text, we adopt a match-LSTM model that we developed earlier for textual entailment (Wang & Jiang, 2016). We further adopt the Pointer Net (Ptr-Net) model developed by Vinyals et al. (2015), which enables the predictions of tokens from the input sequence only rather than from a larger fixed vocabulary and thus allows us to generate answers that consist of multiple tokens from the original text. We propose two ways to apply the Ptr-Net model for our task: a sequence model and a boundary model. We also further extend the boundary model with a search mechanism. Experiments on the SQuAD dataset show that our two models both outperform the best performance reported by Rajpurkar et al. (2016). Moreover, using an ensemble of several of our models, we can achieve very competitive performance on SQuAD.

Our contributions<sup>1</sup> can be summarized as follows: (1) We propose two new end-to-end neural network models for machine comprehension, which combine match-LSTM and Ptr-Net to handle the special properties of the SQuAD dataset. (2) We have achieved the performance of an exact match score of  $67.9\%$  and an F1 score of  $77.0\%$  on the unseen test dataset, which is much better than the feature-engineered solution (Rajpurkar et al., 2016). Our performance is also close to the state of the art on SQuAD, which is  $71.6\%$  in terms of exact match and  $80.4\%$  in terms of F1 from Salesforce Research. (3) Our further analyses of the models reveal some useful insights for further improving the method.

# 2 METHOD

In this section, we first briefly review match-LSTM and Pointer Net. These two pieces of existing work lay the foundation of our method. We then present our end-to-end neural architecture for machine comprehension.

# 2.1 MATCH-LSTM

In a recent work on learning natural language inference, we proposed a match-LSTM model for predicting textual entailment (Wang & Jiang, 2016). In textual entailment, two sentences are given where one is a premise and the other is a hypothesis. To predict whether the premise entails the hypothesis, the match-LSTM model goes through the tokens of the hypothesis sequentially. At each position of the hypothesis, attention mechanism is used to obtain a weighted vector representation of the premise. This weighted premise is then to be combined with a vector representation of the current token of the hypothesis and fed into an LSTM, which we call the match-LSTM. The match-LSTM essentially sequentially aggregates the matching of the attention-weighted premise to each token of the hypothesis and uses the aggregated matching result to make a final prediction.

![](images/bd4007c56e3b5fe609ba5a996e85e2a66f78fe74a302d9009685e6a53e07988d.jpg)  
Figure 1: An overview of our two models. Both models consist of an LSTM preprocessing layer, a match-LSTM layer and an Answer Pointer layer. For each match-LSTM in a particular direction,  $\bar{h}_i^{\mathrm{q}}$ , which is defined as  $\mathbf{H}^{\mathrm{q}}\alpha_{i}^{\top}$ , is computed using the  $\alpha$  in the corresponding direction, as described in either Eqn. (2) or Eqn. (5).

![](images/8216b84684983ff75b109647aaf78b483a5e8bb8f58658132d0f5f63c3a29d7e.jpg)

# 2.2 POINTER NET

Vinyals et al. (2015) proposed a Pointer Network (Ptr-Net) model to solve a special kind of problems where we want to generate an output sequence whose tokens must come from the input sequence. Instead of picking an output token from a fixed vocabulary, Ptr-Net uses attention mechanism as a pointer to select a position from the input sequence as an output symbol. The pointer mechanism has inspired some recent work on language processing (Gu et al., 2016; Kadlec et al., 2016). Here we adopt Ptr-Net in order to construct answers using tokens from the input text.

# 2.3 OUR METHOD

Formally, the problem we are trying to solve can be formulated as follows. We are given a piece of text, which we refer to as a passage, and a question related to the passage. The passage is represented by matrix  $\mathbf{P} \in \mathbb{R}^{d \times P}$ , where  $P$  is the length (number of tokens) of the passage and  $d$  is the dimensionality of word embeddings. Similarly, the question is represented by matrix  $\mathbf{Q} \in \mathbb{R}^{d \times Q}$  where  $Q$  is the length of the question. Our goal is to identify a subsequence from the passage as the answer to the question.

As pointed out earlier, since the output tokens are from the input, we would like to adopt the Pointer Net for this problem. A straightforward way of applying Ptr-Net here is to treat an answer as a sequence of tokens from the input passage but ignore the fact that these tokens are consecutive in the original passage, because Ptr-Net does not make the consecutivity assumption. Specifically, we represent the answer as a sequence of integers  $\mathbf{a} = (a_{1},a_{2},\ldots)$ , where each  $a_{i}$  is an integer between 1 and  $P$ , indicating a certain position in the passage.

Alternatively, if we want to ensure consecutivity, that is, if we want to ensure that we indeed select a subsequence from the passage as an answer, we can use the  $\mathrm{Ptr - Net}$  to predict only the start and the end of an answer. In this case, the  $\mathrm{Ptr - Net}$  only needs to select two tokens from the input passage, and all the tokens between these two tokens in the passage are treated as the answer. Specifically, we can represent the answer to be predicted as two integers  $\mathbf{a} = (a_{\mathrm{s}},a_{\mathrm{e}})$ , where  $a_{\mathrm{s}}$  an  $a_{\mathrm{e}}$  are integers between 1 and  $P$ .

We refer to the first setting above as a sequence model and the second setting above as a boundary model. For either model, we assume that a set of training examples in the form of triplets  $\{(\mathbf{P}_n,\mathbf{Q}_n,\mathbf{a}_n)\}_{n = 1}^N$  are given.

An overview of the two neural network models are shown in Figure 1. Both models consist of three layers: (1) An LSTM preprocessing layer that preprocesses the passage and the question using LSTMs. (2) A match-LSTM layer that tries to match the passage against the question. (3) An Answer Pointer (Ans-Ptr) layer that uses Prr-Net to select a set of tokens from the passage as the answer. The difference between the two models only lies in the third layer.

# LSTM Preprocessing Layer

The purpose for the LSTM preprocessing layer is to incorporate contextual information into the representation of each token in the passage and the question. We use a standard one-directional LSTM (Hochreiter & Schmidhuber, 1997)  ${}^{2}$  to process the passage and the question separately, as shown below:

$$
\mathbf {H} ^ {\mathrm {p}} = \overrightarrow {L S T M} (\mathbf {P}), \quad \mathbf {H} ^ {\mathrm {q}} = \overrightarrow {L S T M} (\mathbf {Q}). \tag {1}
$$

The resulting matrices  $\mathbf{H}^{\mathrm{p}}\in \mathbb{R}^{l\times P}$  and  $\mathbf{H}^{\mathrm{q}}\in \mathbb{R}^{l\times Q}$  are hidden representations of the passage and the question, where  $l$  is the dimensionality of the hidden vectors. In other words, the  $i^{\mathrm{th}}$  column vector  $\mathbf{h}_i^{\mathrm{p}}$  (or  $\mathbf{h}_i^{\mathrm{q}}$ ) in  $\mathbf{H}^{\mathrm{p}}$  (or  $\mathbf{H}^{\mathrm{q}}$ ) represents the  $i^{\mathrm{th}}$  token in the passage (or the question) together with some contextual information from the left.

# Match-LSTM Layer

We apply the match-LSTM model (Wang & Jiang, 2016) proposed for textual entailment to our machine comprehension problem by treating the question as a premise and the passage as a hypothesis. The match-LSTM sequentially goes through the passage. At position  $i$  of the passage, it first uses the standard word-by-word attention mechanism to obtain attention weight vector  $\vec{\alpha}_i \in \mathbb{R}^Q$  as follows:

$$
\begin{array}{l} \overrightarrow {\mathbf {G}} _ {i} = \tanh  (\mathbf {W} ^ {\mathrm {q}} \mathbf {H} ^ {\mathrm {q}} + (\mathbf {W} ^ {\mathrm {p}} \mathbf {h} _ {i} ^ {\mathrm {p}} + \mathbf {W} ^ {\mathrm {r}} \overrightarrow {\mathbf {h}} _ {i - 1} ^ {\mathrm {r}} + \mathbf {b} ^ {\mathrm {p}}) \otimes \mathbf {e} _ {Q}), \\ \vec {\alpha} _ {i} = \operatorname {s o f t m a x} \left(\mathbf {w} ^ {\intercal} \overrightarrow {\mathbf {G}} _ {i} + b \otimes \mathbf {e} _ {Q}\right), \tag {2} \\ \end{array}
$$

where  $\mathbf{W}^{\mathrm{q}},\mathbf{W}^{\mathrm{p}},\mathbf{W}^{\mathrm{r}}\in \mathbb{R}^{l\times l},\mathbf{b}^{\mathrm{p}},\mathbf{w}\in \mathbb{R}^{l}$  and  $b\in \mathbb{R}$  are parameters to be learned,  $\vec{\mathbf{h}}_{i - 1}^{\mathrm{r}}\in \mathbb{R}^{l}$  is the hidden vector of the one-directional match-LSTM (to be explained below) at position  $i - 1$  , and the outer product  $(\cdot \otimes \mathbf{e}_Q)$  produces a matrix or row vector by repeating the vector or scalar on the left for  $Q$  times.

Essentially, the resulting attention weight  $\overrightarrow{\alpha}_{i,j}$  above indicates the degree of matching between the  $i^{\text{th}}$  token in the passage with the  $j^{\text{th}}$  token in the question. Next, we use the attention weight vector  $\overrightarrow{\alpha}_i$  to obtain a weighted version of the question and combine it with the current token of the passage to form a vector  $\overrightarrow{\mathbf{z}}_i$ :

$$
\overrightarrow {\mathbf {z}} _ {i} = \left[ \begin{array}{l} \mathbf {h} _ {i} ^ {\mathrm {p}} \\ \mathbf {H} ^ {\mathrm {q}} \overrightarrow {\alpha_ {i} ^ {\mathrm {T}}} \end{array} \right]. \tag {3}
$$

This vector  $\vec{\mathbf{z}}_i$  is fed into a standard one-directional LSTM to form our so-called match-LSTM:

$$
\vec {\mathbf {h}} _ {i} ^ {\mathrm {r}} = \overrightarrow {L S T M} \left(\vec {\mathbf {z}} _ {i}, \vec {\mathbf {h}} _ {i - 1} ^ {\mathrm {r}}\right), \tag {4}
$$

where  $\overrightarrow{\mathbf{h}}_i^{\mathrm{r}}\in \mathbb{R}^l$

We further build a similar match-LSTM in the reverse direction. The purpose is to obtain a representation that encodes the contexts from both directions for each token in the passage. To build this reverse match-LSTM, we first define

$$
\begin{array}{l} \stackrel {\leftarrow} {\mathbf {G}} _ {i} = \tanh  \left(\mathbf {W} ^ {\mathrm {q}} \mathbf {H} ^ {\mathrm {q}} + \left(\mathbf {W} ^ {\mathrm {p}} \mathbf {h} _ {i} ^ {\mathrm {p}} + \mathbf {W} ^ {\mathrm {r}} \overleftarrow {\mathbf {h}} _ {i + 1} ^ {\mathrm {r}} + \mathbf {b} ^ {\mathrm {p}}\right) \otimes \mathbf {e} _ {Q}\right), \\ \overleftarrow {\alpha} _ {i} = \operatorname {s o f t m a x} \left(\mathbf {w} ^ {\intercal} \overleftarrow {\mathbf {G}} _ {i} + b \otimes \mathbf {e} _ {Q}\right). \tag {5} \\ \end{array}
$$

Note that the parameters here  $(\mathbf{W}^{\mathrm{q}},\mathbf{W}^{\mathrm{p}},\mathbf{W}^{\mathrm{r}},\mathbf{b}^{\mathrm{p}},\mathbf{w}$  and  $b)$  are the same as used in Eqn. (2). We then define  $\overleftarrow{\mathbf{z}}_i$  in a similar way and finally define  $\overleftarrow{\mathbf{h}}_i^{\mathrm{r}}$  to be the hidden representation at position  $i$  produced by the match-LSTM in the reverse direction.

Let  $\vec{\mathbf{H}}^{\mathrm{r}}\in \mathbb{R}^{l\times P}$  represent the hidden states  $[\vec{\mathbf{h}}_1^{\mathrm{r}},\vec{\mathbf{h}}_2^{\mathrm{r}},\dots ,\vec{\mathbf{h}}_P^{\mathrm{r}}]$  and  $\overleftarrow{\mathbf{H}}^{\mathrm{r}}\in \mathbb{R}^{l\times P}$  represent  $[\overleftarrow{\mathbf{h}}_{1}^{\mathrm{r}},\overleftarrow{\mathbf{h}}_{2}^{\mathrm{r}},\dots ,\overleftarrow{\mathbf{h}}_{P}^{\mathrm{r}}]$ . We define  $\mathbf{H}^{\mathrm{r}}\in \mathbb{R}^{2l\times P}$  as the concatenation of the two:

$$
\mathbf {H} ^ {\mathrm {r}} = \left[ \begin{array}{l} \overrightarrow {\mathbf {H}} ^ {\mathrm {r}} \\ \leftarrow \overline {{\mathbf {H}}} ^ {\mathrm {r}} \end{array} \right]. \tag {6}
$$

# Answer Pointer Layer

The top layer, the Answer Pointer (Ans-Ptr) layer, is motivated by the Pointer Net introduced by Vinyals et al. (2015). This layer uses the sequence  $\mathbf{H}^{\mathrm{r}}$  as input. Recall that we have two different models: The sequence model produces a sequence of answer tokens but these tokens may not be consecutive in the original passage. The boundary model produces only the start token and the end token of the answer, and then all the tokens between these two in the original passage are considered to be the answer. We now explain the two models separately.

The Sequence Model: Recall that in the sequence model, the answer is represented by a sequence of integers  $\mathbf{a} = (a_{1},a_{2},\ldots)$  indicating the positions of the selected tokens in the original passage. The Ans-Ptr layer models the generation of these integers in a sequential manner. Because the length of an answer is not fixed, in order to stop generating answer tokens at certain point, we allow each  $a_{k}$  to take up an integer value between 1 and  $P + 1$ , where  $P + 1$  is a special value indicating the end of the answer. Once  $a_{k}$  is set to be  $P + 1$ , the generation of the answer stops.

In order to generate the  $k^{\mathrm{th}}$  answer token indicated by  $a_{k}$ , first, the attention mechanism is used again to obtain an attention weight vector  $\beta_{k} \in \mathbb{R}^{(P + 1)}$ , where  $\beta_{k,j}$  ( $1 \leq j \leq P + 1$ ) is the probability of selecting the  $j^{\mathrm{th}}$  token from the passage as the  $k^{\mathrm{th}}$  token in the answer, and  $\beta_{k,(P + 1)}$  is the probability of stopping the answer generation at position  $k$ .  $\beta_{k}$  is modeled as follows:

$$
\mathbf {F} _ {k} = \tanh  \left(\mathbf {V} \widetilde {\mathbf {H}} ^ {\mathrm {r}} + \left(\mathbf {W} ^ {\mathrm {a}} \mathbf {h} _ {k - 1} ^ {\mathrm {a}} + \mathbf {b} ^ {a}\right) \otimes \mathbf {e} _ {(P + 1)}\right), \tag {7}
$$

$$
\beta_ {k} = \operatorname {s o f t m a x} \left(\mathbf {v} ^ {\top} \mathbf {F} _ {k} + c \otimes \mathbf {e} _ {(P + 1)}\right), \tag {8}
$$

where  $\widetilde{\mathbf{H}}^{\mathrm{r}}\in \mathbb{R}^{2l\times (P + 1)}$  is the concatenation of  $\mathbf{H}^{\mathrm{r}}$  with a zero vector, defined as  $\widetilde{\mathbf{H}}^{\mathrm{r}} = [\mathbf{H}^{\mathrm{r}};\mathbf{0}]$ ,  $\mathbf{V}\in \mathbb{R}^{l\times 2l}$ ,  $\mathbf{W}^{\mathrm{a}}\in \mathbb{R}^{l\times l}$ ,  $\mathbf{b}^{\mathrm{a}}$ ,  $\mathbf{v}\in \mathbb{R}^{l}$  and  $c\in \mathbb{R}$  are parameters to be learned,  $(\cdot \otimes \mathbf{e}_{(P + 1)})$  follows the same definition as before, and  $\mathbf{h}_{k - 1}^{\mathrm{a}}\in \mathbb{R}^{l}$  is the hidden vector at position  $k - 1$  of an answer LSTM as defined below:

$$
\mathbf {h} _ {k} ^ {\mathrm {a}} = \overrightarrow {L S T M} \left(\widetilde {\mathbf {H}} ^ {\mathrm {r}} \beta_ {k} ^ {\top}, \mathbf {h} _ {k - 1} ^ {\mathrm {a}}\right). \tag {9}
$$

We can then model the probability of generating the answer sequence as

$$
p (\mathbf {a} | \mathbf {H} ^ {\mathrm {r}}) = \prod_ {k} p \left(a _ {k} \mid a _ {1}, a _ {2}, \dots , a _ {k - 1}, \mathbf {H} ^ {\mathrm {r}}\right), \tag {10}
$$

and

$$
p \left(a _ {k} = j \mid a _ {1}, a _ {2}, \dots , a _ {k - 1}, \mathbf {H} ^ {\mathrm {r}}\right) = \beta_ {k, j}. \tag {11}
$$

To train the model, we minimize the following loss function based on the training examples:

$$
- \sum_ {n = 1} ^ {N} \log p \left(\mathbf {a} _ {n} \mid \mathbf {P} _ {n}, \mathbf {Q} _ {n}\right). \tag {12}
$$

The Boundary Model: The boundary model works in a way very similar to the sequence model above, except that instead of predicting a sequence of indices  $a_1, a_2, \ldots$ , we only need to predict two indices  $a_{\mathrm{s}}$  and  $a_{\mathrm{e}}$ . So the main difference from the sequence model above is that in the boundary model we do not need to add the zero padding to  $\mathbf{H}^{\mathrm{r}}$ , and the probability of generating an answer is simply modeled as

$$
p (\mathbf {a} | \mathbf {H} ^ {\mathrm {r}}) = p \left(a _ {\mathrm {s}} \mid \mathbf {H} ^ {\mathrm {r}}\right) p \left(a _ {\mathrm {e}} \mid a _ {\mathrm {s}}, \mathbf {H} ^ {\mathrm {r}}\right). \tag {13}
$$

<table><tr><td rowspan="2"></td><td rowspan="2">d</td><td rowspan="2">|θ|</td><td colspan="2">Exact Match</td><td colspan="2">F1</td></tr><tr><td>Dev</td><td>Test</td><td>Dev</td><td>Test</td></tr><tr><td>Random Guess</td><td>-</td><td>0</td><td>1.1</td><td>1.3</td><td>4.1</td><td>4.3</td></tr><tr><td>Logistic Regression</td><td>-</td><td>-</td><td>40.0</td><td>40.4</td><td>51.0</td><td>51.0</td></tr><tr><td>DCR</td><td>-</td><td>-</td><td>62.5</td><td>62.5</td><td>71.2</td><td>71.0</td></tr><tr><td>Match-LSTM with Ans-Ptr (Sequence)</td><td>150</td><td>882K</td><td>54.4</td><td>-</td><td>68.2</td><td>-</td></tr><tr><td>Match-LSTM with Ans-Ptr (Boundary)</td><td>150</td><td>882K</td><td>61.1</td><td>-</td><td>71.2</td><td>-</td></tr><tr><td>Match-LSTM with Ans-Ptr (Boundary+Search)</td><td>150</td><td>882K</td><td>63.0</td><td>-</td><td>72.7</td><td>-</td></tr><tr><td>Match-LSTM with Ans-Ptr (Boundary+Search)</td><td>300</td><td>3.2M</td><td>63.1</td><td>-</td><td>72.7</td><td>-</td></tr><tr><td>Match-LSTM with Ans-Ptr (Boundary+Search+b)</td><td>150</td><td>1.1M</td><td>63.4</td><td>-</td><td>73.0</td><td>-</td></tr><tr><td>Match-LSTM with Bi-Ans-Ptr (Boundary+Search+b)</td><td>150</td><td>1.4M</td><td>64.1</td><td>64.7</td><td>73.9</td><td>73.7</td></tr><tr><td>Match-LSTM with Ans-Ptr (Boundary+Search+en)</td><td>150</td><td>882K</td><td>67.6</td><td>67.9</td><td>76.8</td><td>77.0</td></tr></table>

Table 2: Experiment Results. Here "Search" refers to globally searching the spans with no more than 15 tokens, "b" refers to using bi-directional pre-processing LSTM, and "en" refers to ensemble method.

We further extend the boundary model by incorporating a search mechanism. Specifically, during prediction, we try to limit the length of the span and globally search the span with the highest probability computed by  $p(\mathbf{a}_s) \times p(\mathbf{a}_e)$ . Besides, as the boundary has a sequence of fixed number of values, bi-directional Ans-Ptr can be simply combined to fine-tune the correct span.

# 3 EXPERIMENTS

In this section, we present our experiment results and perform some analyses to better understand how our models work.

# 3.1 DATA

We use the Stanford Question Answering Dataset (SQuAD) v1.1 to conduct our experiments. Passages in SQuAD come from 536 articles from Wikipedia covering a wide range of topics. Each passage is a single paragraph from a Wikipedia article, and each passage has around 5 questions associated with it. In total, there are 23,215 passages and 107,785 questions. The data has been split into a training set (with 87,599 question-answer pairs), a development set (with 10,570 question-answer pairs) and a hidden test set.

# 3.2 EXPERIMENT SETTINGS

We first tokenize all the passages, questions and answers. The resulting vocabulary contains 117K unique words. We use word embeddings from GloVe (Pennington et al., 2014) to initialize the model. Words not found in GloVe are initialized as zero vectors. The word embeddings are not updated during the training of the model.

The dimensionality  $l$  of the hidden layers is set to be 150 or 300. We use ADAMAX (Kingma & Ba, 2015) with the coefficients  $\beta_{1} = 0.9$  and  $\beta_{2} = 0.999$  to optimize the model. Each update is computed through a minibatch of 30 instances. We do not use L2-regularization.

The performance is measured by two metrics: percentage of exact match with the ground truth answers, and word-level F1 score when comparing the tokens in the predicted answers with the tokens in the ground truth answers. Note that in the development set and the test set each question has around three ground truth answers. F1 scores with the best matching answers are used to compute the average F1 score.

# 3.3 RESULTS

The results of our models as well as the results of the baselines given by Rajpurkar et al. (2016) and Yu et al. (2016) are shown in Table 2. We can see that both of our two models have clearly outper

![](images/b1563131b5a75851a4cbb624eb1f30c67ea58a76d92f4e03075b79ead274e1c4.jpg)

![](images/ac5fa5f02059193c692ad00be4aff448d608aca662ef82312dca8de99dba9487.jpg)

![](images/26fca2338d275ab0466448c8657575ae2d4d9aecd8ef7d3f49fc70b7301e2399.jpg)  
Figure 2: Visualization of the attention weights  $\alpha$  for three questions associated with the same passage.

formed the logistic regression model by Rajpurkar et al. (2016), which relies on carefully designed features. Furthermore, our boundary model has outperformed the sequence model, achieving an exact match score of  $61.1\%$  and an F1 score of  $71.2\%$ . In particular, in terms of the exact match score, the boundary model has a clear advantage over the sequence model. The improvement of our models over the logistic regression model shows that our end-to-end neural network models without much feature engineering are very effective on this task and this dataset. Considering the effectiveness of boundary model, we further explore this model. Observing that most of the answers are the spans with relatively small sizes, we simply limit the largest predicted span to have no more than 15 tokens and conducted experiment with span searching. This resulted in  $1.5\%$  improvement in F1 on the development data and that outperformed the DCR model (Yu et al., 2016), which also introduced some language features such as POS and NE into their model. Besides, we tried to increase the memory dimension in the model by adding bi-directional pre-processing LSTM and bi-directional Ans-Ptr. The improvement on the development data using the first two methods is quite small. By adding Bi-Ans-Ptr with bi-directional pre-processing LSTM, we can get  $1.2\%$  improvement in F1. Finally, we explore the ensemble method by simply computing the product of the boundary probabilities collected from 5 boundary models and then searching the most likely span with no more than 15 tokens. This ensemble method achieved the best performance as shown in the table.

# 3.4 FURTHER ANALYSES

To better understand the strengths and weaknesses of our models, we perform some further analyses of the results below.

First, we suspect that longer answers are harder to predict. To verify this hypothesis, we analysed the performance in terms of both exact match and F1 score with respect to the answer length on the development set. For example, for questions whose answers contain more than 9 tokens, the F1 score of the boundary model drops to around  $55\%$  and the exact match score drops to only around  $30\%$ , compared to the F1 score and exact match score of close to  $72\%$  and  $67\%$ , respectively, for questions with single-token answers. And that supports our hypothesis.

Next, we analyze the performance of our models on different groups of questions. We use a crude way to split the questions into different groups based on a set of question words we have defined, including "what," "how," "who," "when," "which," "where," and "why." These different question words roughly refer to questions with different types of answers. For example, "when" questions look for temporal expressions as answers, whereas "where" questions look for locations as answers. According to the performance on the development data set, our models work the best for "when" questions. This may be because in this dataset temporal expressions are relatively easier to recognize. Other groups of questions whose answers are noun phrases, such as "what" questions, "which" questions and "where" questions, also get relatively better results. On the other hand, "why" questions are the hardest to answer. This is not surprising because the answers to "why" questions can be very diverse, and they are not restricted to any certain type of phrases.

Finally, we would like to check whether the attention mechanism used in the match-LSTM layer is effective in helping the model locate the answer. We show the attention weights  $\alpha$  in Figure 2. In the figure the darker the color is the higher the weight is. We can see that some words have been well aligned based on the attention weights. For example, the word "German" in the passage is aligned well to the word "language" in the first question, and the model successfully predicts "German" as the answer to the question. For the question word "who" in the second question, the word "teacher" actually receives relatively higher attention weight, and the model has predicted the phrase "Martin Sekulic" after that as the answer, which is correct. For the last question that starts with "why", the attention weights are more evenly distributed and it is not clear which words have been aligned to "why".

# 4 RELATED WORK

Machine comprehension of text has gained much attention in recent years, and increasingly researchers are building data-drive, end-to-end neural network models for the task. We will first review the recently released datasets and then some end-to-end models on this task.

# 4.1 DATASETS

A number of datasets for studying machine comprehension were created in Cloze style by removing a single token from a sentence in the original corpus, and the task is to predict the missing word. For example, Hermann et al. (2015) created questions in Cloze style from CNN and Daily Mail highlights. Hill et al. (2016) created the Children's Book Test dataset, which is based on children's stories. Cui et al. (2016) released two similar datasets in Chinese, the People Daily dataset and the Children's Fairy Tale dataset.

Instead of creating questions in Cloze style, a number of other datasets rely on human annotators to create real questions. Richardson et al. (2013) created the well-known MCTest dataset and Tapaswi et al. (2016) created the MovieQA dataset. In these datasets, candidate answers are provided for each question. Similar to these two datasets, the SQuAD dataset (Rajpurkar et al., 2016) was also created by human annotators. Different from the previous two, however, the SQuAD dataset does not provide candidate answers, and thus all possible subsequences from the given passage have to be considered as candidate answers.

Besides the datasets above, there are also a few other datasets created for machine comprehension, such as WikiReading dataset (Hewlett et al., 2016) and bAbI dataset (Weston et al., 2016), but they are quite different from the datasets above in nature.

# 4.2 END-TO-END NEURAL NETWORK MODELS FOR MACHINE COMPREHENSION

There have been a number of studies proposing end-to-end neural network models for machine comprehension. A common approach is to use recurrent neural networks (RNNs) to process the given text and the question in order to predict or generate the answers (Hermann et al., 2015). Attention mechanism is also widely used on top of RNNs in order to match the question with the given passage (Hermann et al., 2015; Chen et al., 2016). Given that answers often come from the given passage, Pointer Network has been adopted in a few studies in order to copy tokens from the given passage as answers (Kadlec et al., 2016; Trischler et al., 2016). Compared with existing

work, we use match-LSTM to match a question and a given passage, and we use Pointer Network in a different way such that we can generate answers that contain multiple tokens from the given passage.

Memory Networks (Weston et al., 2015) have also been applied to machine comprehension (Sukhbaatar et al., 2015; Kumar et al., 2016; Hill et al., 2016), but its scalability when applied to a large dataset is still an issue. In this work, we did not consider memory networks for the SQuAD dataset.

# 5 CONCLUSIONS

In this paper, We developed two models for the machine comprehension problem defined in the Stanford Question Answering (SQuAD) dataset, both making use of match-LSTM and Pointer Network. Experiments on the SQuAD dataset showed that our second model, the boundary model, could achieve an exact match score of  $67.6\%$  and an F1 score of  $77\%$  on the test dataset, which is better than our sequence model and Rajpurkar et al. (2016)'s feature-engineered model.

In the future, we plan to look further into the different types of questions and focus on those questions which currently have low performance, such as the "why" questions. We also plan to test how our models could be applied to other machine comprehension datasets.

# 6 ACKNOWLEDGMENTS

We thank Pranav Rajpurkar for testing our model on the hidden test dataset and Percy Liang for helping us with the Dockerfile for Codalab.

# REFERENCES

Danqi Chen, Jason Bolton, and Christopher D. Manning. A thorough examination of the CNN/Daily Mail reading comprehension task. In Proceedings of the Conference on Association for Computational Linguistics, 2016.  
Yiming Cui, Ting Liu, Zhipeng Chen, Shijin Wang, and Guoping Hu. Consensus attention-based neural networks for Chinese reading comprehension. In arXiv preprint arXiv:1607.02250, 2016.  
Jiatao Gu, Zhengdong Lu, Hang Li, and Victor O.K. Li. Incorporating copying mechanism in sequence-to-sequence learning. In Proceedings of the Conference on Association for Computational Linguistics, 2016.  
Karl Moritz Hermann, Tomas Kocisky, Edward Grefenstette, Lasse Espeholt, Will Kay, Mustafa Suleyman, and Phil Blunsom. Teaching machines to read and comprehend. In Proceedings of the Conference on Advances in Neural Information Processing Systems, pp. 1693-1701, 2015.  
Daniel Hewlett, Alexandre Lacoste, Llion Jones, Illia Polosukhin, Andrew Fandrianto, Jay Han, Matthew Kelcey, and David Berthelot. WIKIREADING: A novel large-scale language understanding task over wikipedia. In Proceedings of the Conference on Association for Computational Linguistics, 2016.  
Felix Hill, Antoine Bordes, Sumit Chopra, and Jason Weston. The Goldilocks principle: Reading children's books with explicit memory representations. In Proceedings of the International Conference on Learning Representations, 2016.  
Sepp Hochreiter and Jürgen Schmidhuber. Long short-term memory. Neural computation, 9(8): 1735-1780, 1997.  
Rudolf Kadlec, Martin Schmid, Ondrej Bajgar, and Jan Kleindienst. Text understanding with the attention sum reader network. In Proceedings of the Conference on Association for Computational Linguistics, 2016.  
Diederik Kingma and Jimmy Ba. Adam: A method for stochastic optimization. In Proceedings of the International Conference on Learning Representations, 2015.

Ankit Kumar, Ozan Irsoy, Jonathan Su, James Bradbury, Robert English, Brian Pierce, Peter Ondruska, Ishaan Gulrajani, and Richard Socher. Ask me anything: Dynamic memory networks for natural language processing. In Proceedings of the International Conference on Machine Learning, 2016.  
Jeffrey Pennington, Richard Socher, and Christopher D Manning. GloVe: Global vectors for word representation. In Proceedings of the Conference on Empirical Methods in Natural Language Processing, 2014.  
Pranav Rajpurkar, Jian Zhang, Konstantin Lopyrev, and Percy Liang. SQuAD: 100,000+ questions for machine comprehension of text. In Proceedings of the Conference on Empirical Methods in Natural Language Processing, 2016.  
Matthew Richardson, Christopher JC Burges, and Erin Renshaw. MCTest: A challenge dataset for the open-domain machine comprehension of text. In Proceedings of the Conference on Empirical Methods in Natural Language Processing, 2013.  
Sainbayar Sukhbaatar, Jason Weston, Rob Fergus, et al. End-to-end memory networks. In Proceedings of the Conference on Advances in neural information processing systems, 2015.  
Makarand Tapaswi, Yukun Zhu, Rainer Stiefelhagen, Antonio Torralba, Raquel Urtasun, and Sanja Fidler. MovieQA: Understanding stories in movies through question-answering. In Proceedings of IEEE Conference on Computer Vision and Pattern Recognition, 2016.  
Adam Trischler, Zheng Ye, Xingdi Yuan, and Kaheer Suleman. Natural language comprehension with the EpiReader. In Proceedings of the Conference on Empirical Methods in Natural Language Processing, 2016.  
Oriol Vinyals, Meire Fortunato, and Navdeep Jaitly. Pointer networks. In Proceedings of the Conference on Advances in Neural Information Processing Systems, 2015.  
Shuohang Wang and Jing Jiang. Learning natural language inference with LSTM. In Proceedings of the Conference on the North American Chapter of the Association for Computational Linguistics, 2016.  
Jason Weston, Sumit Chopra, and Antoine Bordes. Memory networks. In Proceedings of the International Conference on Learning Representations, 2015.  
Jason Weston, Antoine Bordes, Sumit Chopra, Alexander M Rush, Bart van Merrienboer, Armand Joulin, and Tomas Mikolov. Towards AI-complete question answering: A set of prerequisite toy tasks. In Proceedings of the International Conference on Learning Representations, 2016.  
Wenpeng Yin, Sebastian Ebert, and Hinrich Schütze. Attention-based convolutional neural network for machine comprehension. arXiv preprint arXiv:1602.04341, 2016.  
Yang Yu, Wei Zhang, Kazi Hasan, Mo Yu, Bing Xiang, and Bowen Zhou. End-to-end answer chunk extraction and ranking for reading comprehension. arXiv preprint arXiv:1610.09996, 2016.

![](images/568c4ca5dc6d29c68f7f13f676271bc8b44a427ff11e6ff0df1330b07be74497.jpg)

![](images/112bed83ae327480cb716584b7d91287731450b693449e0309eeda212bd014ee.jpg)

![](images/e072633bd1435827bc6887c467a3f052f0a68843afde9819ed9aee8a1d1cd74d.jpg)  
Figure 3: Performance breakdown by answer lengths and question types. Top: Plot (1) shows the performance of our two models (where  $s$  refers to the sequence model,  $b$  refers to the boundary model, and  $e$  refers to the ensemble boundary model) over answers with different lengths. Plot (2) shows the numbers of answers with different lengths. Bottom: Plot (3) shows the performance our the two models on different types of questions. Plot (4) shows the numbers of different types of questions.

![](images/7d3bc85c8694169c065aa93cab1bb1a47e16969dfa64ce291bc941ae54851255.jpg)
