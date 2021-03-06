# Copyright (c) 2017-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the license found in the LICENSE file in
# the root directory of this source tree. An additional grant of patent rights
# can be found in the PATENTS file in the same directory.
# TODO : set back

import math

import torch
from fairseq import utils

from . import FairseqCriterion, register_criterion


@register_criterion('align_label_smoothed_cross_entropy')
class AlignLabelSmoothedCrossEntropyCriterion(FairseqCriterion):

    def __init__(self, args, task):
        super().__init__(args, task)
        self.eps = args.label_smoothing
        self.alpha = args.regul_align_scale

    @staticmethod
    def add_args(parser):
        """Add criterion-specific arguments to the parser."""
        # fmt: off
        parser.add_argument('--label-smoothing', default=0., type=float, metavar='D',
                            help='epsilon for label smoothing, 0 means no label smoothing')
        parser.add_argument('--regul-align-scale', default=0., type=float)
        # fmt: on

    def forward(self, model, sample, step=-1, epoche=-1, reduce=True):
        """Compute the loss for the given sample.

        Returns a tuple with three elements:
        1) the loss
        2) the sample size, which is used as the denominator for the gradient
        3) logging outputs to display while training
        """
        net_output = model(**sample['net_input'])
        writing_loss, nll_loss = self.compute_loss(model, net_output, sample, reduce=reduce)
        align_loss = self.compute_alignment_loss(net_output, sample)
        loss = writing_loss + self.alpha * align_loss
        sample_size = sample['target'].size(0) if self.args.sentence_avg else sample['ntokens']
        logging_output = {
            'loss': utils.item(loss.data) if reduce else loss.data,
            'writing_loss': utils.item(writing_loss.data) if reduce else writing_loss.data,
            'regul_loss': utils.item(align_loss.data) if reduce else align_loss.data,
            'nll_loss': utils.item(nll_loss.data) if reduce else nll_loss.data,
            'ntokens': sample['ntokens'],
            'nsentences': sample['target'].size(0),
            'sample_size': sample_size,
        }
        return loss, sample_size, logging_output

    def compute_alignment_loss(self, net_output, sample):
        target = sample['target'].view(-1, 1)  # B*Tt, 1
        non_pad_mask = target.ne(self.padding_idx)
        attention = net_output[1]  # B, Tt, Ts
        # print('Attention:', attention)
        attention = - torch.log(attention + 1e-5)
        B, Tt, Ts = attention.size()
        labels = sample['contexts']  # B, Tt (values in 1...Ts)
        labels = labels.view(-1, 1)  # B*Tt, 1
        loss =  attention.contiguous().view(-1, Ts).gather(dim=-1, index=labels-1)[non_pad_mask]
        loss = loss.sum()
        return loss

    def compute_loss(self, model, net_output, sample, reduce=True):
        lprobs = model.get_normalized_probs(net_output, log_probs=True)
        lprobs = lprobs.view(-1, lprobs.size(-1))
        target = model.get_targets(sample, net_output).view(-1, 1)
        non_pad_mask = target.ne(self.padding_idx)
        nll_loss = -lprobs.gather(dim=-1, index=target)[non_pad_mask]
        smooth_loss = -lprobs.sum(dim=-1, keepdim=True)[non_pad_mask]
        if reduce:
            nll_loss = nll_loss.sum()
            smooth_loss = smooth_loss.sum()
        eps_i = self.eps / lprobs.size(-1)
        loss = (1. - self.eps) * nll_loss + eps_i * smooth_loss
        return loss, nll_loss

    @staticmethod
    def aggregate_logging_outputs(logging_outputs):
        """Aggregate logging outputs from data parallel training."""
        ntokens = sum(log.get('ntokens', 0) for log in logging_outputs)
        nsentences = sum(log.get('nsentences', 0) for log in logging_outputs)
        sample_size = sum(log.get('sample_size', 0) for log in logging_outputs)
        return {
            'loss': sum(log.get('loss', 0) for log in logging_outputs) / sample_size / math.log(2),
            'nll_loss': sum(log.get('nll_loss', 0) for log in logging_outputs) / ntokens / math.log(2),
            'writing_loss': sum(log.get('writing_loss', 0) for log in logging_outputs) / ntokens / math.log(2),
            'regul_loss': sum(log.get('regul_loss', 0) for log in logging_outputs) / ntokens / math.log(2),
            'ntokens': ntokens,
            'nsentences': nsentences,
            'sample_size': sample_size,
        }
