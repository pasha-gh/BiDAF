# Copyright 2018 Stanford University
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""This file contains some basic model components"""

import tensorflow as tf
from tensorflow.python.ops.rnn_cell import DropoutWrapper
from tensorflow.python.ops import variable_scope as vs
from tensorflow.python.ops import rnn_cell
from tensorflow.nn import relu
'''
class Highway(object):

  def __init__(self, text_size, hidden_size, keep_prob):
      self.text_size = text_size
      self.hidden_size = hidden_size
      self.keep_prob = keep_prob

  def build_graph(self, x, carry_bias=-1.0):
        W_T = tf.Variable(tf.truncated_normal([self.hidden_size, self.hidden_size], stddev=0.1), name="weight_transform")
        b_T = tf.Variable(tf.constant(carry_bias, shape=[self.hidden_size]), name="bias_transform")

        W = tf.Variable(tf.truncated_normal([self.hidden_size, self.hidden_size], stddev=0.1), name="weight")
        b = tf.Variable(tf.constant(0.1, shape=[self.hidden_size]), name="bias")

        T = tf.sigmoid(tf.tensordot(x, W_T, axes=[[2],[1]]) + b_T, name="transform_gate")
        H = relu(tf.tensordot(x, W, axes=[[2],[1]]) + b, name="activation")
        C = tf.subtract(1.0, T, name="carry_gate")

        y = tf.add(tf.multiply(H, T), tf.multiply(x, C), "y")
        return y
'''

class Highway(object):
    def __init__(self, hidden_size):
        self.hidden_size = hidden_size # Should be equal to embedding size?

    def build_graph(self, inputs):
        activation = tf.contrib.layers.fully_connected(inputs, num_outputs=self.hidden_size) # shape (batch_size, seq_len, 1)
        transform = tf.contrib.layers.fully_connected(inputs, num_outputs=self.hidden_size, activation_fn=None)
        transform = tf.sigmoid(transform)

        carry_gate = tf.subtract(1.0, transform)
        output = tf.add(tf.multiply(transform, activation), tf.multiply(carry_gate, inputs))
        return output

class RNNEncoder(object):
    """
    General-purpose module to encode a sequence using a RNN.
    It feeds the input through a RNN and returns all the hidden states.

    Note: In lecture 8, we talked about how you might use a RNN as an "encoder"
    to get a single, fixed size vector representation of a sequence
    (e.g. by taking element-wise max of hidden states).
    Here, we're using the RNN as an "encoder" but we're not taking max;
    we're just returning all the hidden states. The terminology "encoder"
    still applies because we're getting a different "encoding" of each
    position in the sequence, and we'll use the encodings downstream in the model.

    This code uses a bidirectional GRU, but you could experiment with other types of RNN.
    """

    def __init__(self, hidden_size, keep_prob):
        """
        Inputs:
          hidden_size: int. Hidden size of the RNN
          keep_prob: Tensor containing a single scalar that is the keep probability (for dropout)
        """
        self.hidden_size = hidden_size
        self.keep_prob = keep_prob
        self.rnn_cell_fw = tf.nn.rnn_cell.MultiRNNCell([rnn_cell.GRUCell(self.hidden_size) for i in range(2)])
        self.rnn_cell_fw = DropoutWrapper(self.rnn_cell_fw, input_keep_prob=self.keep_prob)
        self.rnn_cell_bw = tf.nn.rnn_cell.MultiRNNCell([rnn_cell.GRUCell(self.hidden_size) for i in range(2)])
        self.rnn_cell_bw = DropoutWrapper(self.rnn_cell_bw, input_keep_prob=self.keep_prob)

    def build_graph(self, inputs, masks):
        """
        Inputs:
          inputs: Tensor shape (batch_size, seq_len, input_size)
          masks: Tensor shape (batch_size, seq_len).
            Has 1s where there is real input, 0s where there's padding.
            This is used to make sure tf.nn.bidirectional_dynamic_rnn doesn't iterate through masked steps.

        Returns:
          out: Tensor shape (batch_size, seq_len, hidden_size*2).
            This is all hidden states (fw and bw hidden states are concatenated).
        """
        with vs.variable_scope("RNNEncoder"):
            input_lens = tf.reduce_sum(masks, reduction_indices=1) # shape (batch_size)

            # Note: fw_out and bw_out are the hidden states for every timestep.
            # Each is shape (batch_size, seq_len, hidden_size).
            (fw_out, bw_out), _ = tf.nn.bidirectional_dynamic_rnn(self.rnn_cell_fw, self.rnn_cell_bw, inputs, input_lens, dtype=tf.float32)

            # Concatenate the forward and backward hidden states
            out = tf.concat([fw_out, bw_out], 2)

            # Apply dropout
            out = tf.nn.dropout(out, self.keep_prob)

            return out


class SimpleSoftmaxLayer(object):
    """
    Module to take set of hidden states, (e.g. one for each context location),
    and return probability distribution over those states.
    """

    def __init__(self):
        pass

    def build_graph(self, inputs, masks):
        """
        Applies one linear downprojection layer, then softmax.

        Inputs:
          inputs: Tensor shape (batch_size, seq_len, hidden_size)
          masks: Tensor shape (batch_size, seq_len)
           Has 1s where there is real input, 0s where there's padding.

        Outputs:
          logits: Tensor shape (batch_size, seq_len)
            logits is the result of the downprojection layer, but it has -1e30
            (i.e. very large negative number) in the padded locations
          prob_dist: Tensor shape (batch_size, seq_len)
            The result of taking softmax over logits.
            This should have 0 in the padded locations, and the rest should sum to 1.
        """
        with vs.variable_scope("SimpleSoftmaxLayer"):

            # Linear downprojection layer
            logits = tf.contrib.layers.fully_connected(inputs, num_outputs=1, activation_fn=None) # shape (batch_size, seq_len, 1)
            logits = tf.squeeze(logits, axis=[2]) # shape (batch_size, seq_len)

            # Take softmax over sequence
            masked_logits, prob_dist = masked_softmax(logits, masks, 1)

            return masked_logits, prob_dist


class BiDAFAttn(object):

    def __init__(self, hidden_size, context_len, question_len):
        self.hidden_size = hidden_size
        self.context_len = context_len
        self.question_len = question_len

    def build_graph(self, context_outputs, question_outputs, context_masks, question_masks):
        with vs.variable_scope("BiDAFAttn"):
            a = tf.tile(context_outputs, [1, self.question_len, 1])
            a = tf.reshape(a, [-1, self.question_len, self.context_len, self.hidden_size * 2])
            a = tf.transpose(a, [0, 2, 1, 3])

            b = tf.tile(question_outputs, [1, self.context_len, 1])
            b = tf.reshape(b, [-1, self.context_len, self.question_len, self.hidden_size * 2])

            c = tf.multiply(a, b)

            x = tf.concat([a, b, c], axis = 3)

            x = tf.contrib.layers.fully_connected(x, num_outputs=1, activation_fn=None) # shape (batch_size, seq_len, seq_len, 1)
            attention_matrix = tf.squeeze(x, axis=[3]) # shape (batch_size, seq_len, seq_len)

            context_to_question_att = tf.nn.softmax(attention_matrix, axis = 1) # U~
            context_to_question_att = tf.matmul(context_to_question_att, question_outputs)

            question_to_context_att = tf.reduce_max(attention_matrix, axis = 2)
            question_to_context_att = tf.nn.softmax(question_to_context_att, axis = 1)
            question_to_context_att = tf.reshape(question_to_context_att, shape=[-1 , self.context_len, 1])

            h_tilde = tf.matmul(context_outputs, question_to_context_att, transpose_a = True)

            question_to_context_att = tf.tile(question_to_context_att, multiples=[1, 1, self.hidden_size * 2])

            hoh_att = tf.multiply(context_outputs, question_to_context_att)

            hou_att = tf.multiply(context_outputs, context_to_question_att)

            return tf.concat([context_outputs, context_to_question_att, hoh_att, hou_att], axis=2)

class Modeling(object):
    def __init__(self, hidden_size):
        """
        Inputs:
          hidden_size: int. Hidden size of the RNN
          keep_prob: Tensor containing a single scalar that is the keep probability (for dropout)
        """
        self.hidden_size = hidden_size
        self.rnn_cell_fw = tf.nn.rnn_cell.MultiRNNCell([rnn_cell.GRUCell(self.hidden_size) for i in range(2)])
        self.rnn_cell_bw = tf.nn.rnn_cell.MultiRNNCell([rnn_cell.GRUCell(self.hidden_size) for i in range(2)])
#        self.rnn_cell_fw = rnn_cell.GRUCell(self.hidden_size)
#        self.rnn_cell_bw = rnn_cell.GRUCell(self.hidden_size)

    def build_graph(self, inputs, masks):
        """
        Inputs:
          inputs: Tensor shape (batch_size, seq_len, input_size)
          masks: Tensor shape (batch_size, seq_len).
            Has 1s where there is real input, 0s where there's padding.
            This is used to make sure tf.nn.bidirectional_dynamic_rnn doesn't iterate through masked steps.

        Returns:
          out: Tensor shape (batch_size, seq_len, hidden_size*2).
            This is all hidden states (fw and bw hidden states are concatenated).
        """
        with vs.variable_scope("Modeling"):
            input_lens = tf.reduce_sum(masks, reduction_indices=1) # shape (batch_size)

            # Note: fw_out and bw_out are the hidden states for every timestep.
            # Each is shape (batch_size, seq_len, hidden_size).
            (fw_out, bw_out), _ = tf.nn.bidirectional_dynamic_rnn(self.rnn_cell_fw, self.rnn_cell_bw, inputs, input_lens, dtype=tf.float32)

            # Concatenate the forward and backward hidden states
            out = tf.concat([fw_out, bw_out], 2)

            return out

class RNNDecoder(object):
   def __init__(self, hidden_size):
        self.hidden_size = hidden_size
        self.rnn_cell_fw = rnn_cell.GRUCell(self.hidden_size)
        self.rnn_cell_bw = rnn_cell.GRUCell(self.hidden_size)

   def build_graph(self, modeling_output, attention_output, masks):
        with vs.variable_scope("RNNDecoder"):
            input_lens = tf.reduce_sum(masks, reduction_indices=1) # shape (batch_size)

            # Note: fw_out and bw_out are the hidden states for every timestep.
            # Each is shape (batch_size, seq_len, hidden_size).
            (fw_out, bw_out), _ = tf.nn.bidirectional_dynamic_rnn(self.rnn_cell_fw, self.rnn_cell_bw, modeling_output, input_lens, dtype=tf.float32)

            # Concatenate the forward and backward hidden states
            out = tf.concat([fw_out, bw_out], 2)

            start_w = tf.get_variable('start_w', shape=[self.hidden_size * 10], dtype=tf.float32,
                                      initializer=tf.initializers.random_normal)
            end_w = tf.get_variable('end_w', shape=[self.hidden_size * 10], dtype=tf.float32,
                                   initializer=tf.initializers.random_normal)

            start_features = tf.concat([attention_output, modeling_output], axis = 2)
            end_features = tf.concat([attention_output, out], axis = 2)

            return start_features, end_features

class BasicAttn(object):
    """Module for basic attention.

    Note: in this module we use the terminology of "keys" and "values" (see lectures).
    In the terminology of "X attends to Y", "keys attend to values".

    In the baseline model, the keys are the context hidden states
    and the values are the question hidden states.

    We choose to use general terminology of keys and values in this module
    (rather than context and question) to avoid confusion if you reuse this
    module with other inputs.
    """

    def __init__(self, keep_prob, key_vec_size, value_vec_size):
        """
        Inputs:
          keep_prob: tensor containing a single scalar that is the keep probability (for dropout)
          key_vec_size: size of the key vectors. int
          value_vec_size: size of the value vectors. int
        """
        self.keep_prob = keep_prob
        self.key_vec_size = key_vec_size
        self.value_vec_size = value_vec_size

    def build_graph(self, values, values_mask, keys):
        """
        Keys attend to values.
        For each key, return an attention distribution and an attention output vector.

        Inputs:
          values: Tensor shape (batch_size, num_values, value_vec_size).
          values_mask: Tensor shape (batch_size, num_values).
            1s where there's real input, 0s where there's padding
          keys: Tensor shape (batch_size, num_keys, value_vec_size)

        Outputs:
          attn_dist: Tensor shape (batch_size, num_keys, num_values).
            For each key, the distribution should sum to 1,
            and should be 0 in the value locations that correspond to padding.
          output: Tensor shape (batch_size, num_keys, hidden_size).
            This is the attention output; the weighted sum of the values
            (using the attention distribution as weights).
        """
        with vs.variable_scope("BasicAttn"):

            # Calculate attention distribution
            values_t = tf.transpose(values, perm=[0, 2, 1]) # (batch_size, value_vec_size, num_values)
            attn_logits = tf.matmul(keys, values_t) # shape (batch_size, num_keys, num_values)
            attn_logits_mask = tf.expand_dims(values_mask, 1) # shape (batch_size, 1, num_values)
            _, attn_dist = masked_softmax(attn_logits, attn_logits_mask, 2) # shape (batch_size, num_keys, num_values). take softmax over values

            # Use attention distribution to take weighted sum of values
            output = tf.matmul(attn_dist, values) # shape (batch_size, num_keys, value_vec_size)

            # Apply dropout
            output = tf.nn.dropout(output, self.keep_prob)

            return attn_dist, output


def masked_softmax(logits, mask, dim):
    """
    Takes masked softmax over given dimension of logits.

    Inputs:
      logits: Numpy array. We want to take softmax over dimension dim.
      mask: Numpy array of same shape as logits.
        Has 1s where there's real data in logits, 0 where there's padding
      dim: int. dimension over which to take softmax

    Returns:
      masked_logits: Numpy array same shape as logits.
        This is the same as logits, but with 1e30 subtracted
        (i.e. very large negative number) in the padding locations.
      prob_dist: Numpy array same shape as logits.
        The result of taking softmax over masked_logits in given dimension.
        Should be 0 in padding locations.
        Should sum to 1 over given dimension.
    """
    exp_mask = (1 - tf.cast(mask, 'float')) * (-1e30) # -large where there's padding, 0 elsewhere
    masked_logits = tf.add(logits, exp_mask) # where there's padding, set logits to -large
    prob_dist = tf.nn.softmax(masked_logits, dim)
    return masked_logits, prob_dist