import numpy

from fuel.datasets import TextFile
from fuel.schemes import ConstantScheme
from fuel.streams import DataStream
from fuel.transformers import (
    Merge, Batch, Filter, Padding, SortMapping, Unpack, Mapping)

import cPickle

def _ensure_special_tokens(vocab, bos_idx=0, eos_idx=0, unk_idx=1):
    """Ensures special tokens exist in the dictionary."""

    # remove tokens if they exist in some other index
    tokens_to_remove = [k for k, v in vocab.items()
                        if v in [bos_idx, eos_idx, unk_idx]]
    for token in tokens_to_remove:
        vocab.pop(token)
    # put corresponding item
    vocab['<S>'] = bos_idx
    vocab['</S>'] = eos_idx
    vocab['<UNK>'] = unk_idx
    return vocab


def _length(sentence_pair):
    """Assumes target is the last element in the tuple."""
    return len(sentence_pair[-1])


class PaddingWithEOS(Padding):
    """Padds a stream with given end of sequence idx."""
    def __init__(self, data_stream, eos_idx, **kwargs):
        kwargs['data_stream'] = data_stream
        self.eos_idx = eos_idx
        super(PaddingWithEOS, self).__init__(**kwargs)

    def get_data(self, request=None):
        if request is not None:
            raise ValueError
        data = list(next(self.child_epoch_iterator))
        data_with_masks = []
        for i, (source, source_data) in enumerate(
                zip(self.data_stream.sources, data)):
            if source not in self.mask_sources:
                data_with_masks.append(source_data)
                continue

            shapes = [numpy.asarray(sample).shape for sample in source_data]
            lengths = [shape[0] for shape in shapes]
            max_sequence_length = max(lengths)
            rest_shape = shapes[0][1:]
            if not all([shape[1:] == rest_shape for shape in shapes]):
                raise ValueError("All dimensions except length must be equal")
            dtype = numpy.asarray(source_data[0],'int16').dtype

            padded_data = numpy.ones(
                (len(source_data), max_sequence_length) + rest_shape,
                dtype=dtype) * self.eos_idx[i]
            for i, sample in enumerate(source_data):
                padded_data[i, :len(sample)] = sample
            data_with_masks.append(padded_data)

            mask = numpy.zeros((len(source_data), max_sequence_length),
                               self.mask_dtype)
            for i, sequence_length in enumerate(lengths):
                mask[i, :sequence_length] = 1
            data_with_masks.append(mask)
        return tuple(data_with_masks)


class _oov_to_unk(object):
    """Maps out of vocabulary token index to unk token index."""
    def __init__(self, src_vocab_size=30000, trg_vocab_size=30000,topical_vocab_size=2000,
                 unk_id=1):
        self.src_vocab_size = src_vocab_size
        self.trg_vocab_size = trg_vocab_size
        self.topical_vocab_size = topical_vocab_size
        self.unk_id = unk_id

    def __call__(self, sentence_pair):
        for x in sentence_pair[2]:
            if x>=self.topical_vocab_size:
                print("error!!");
        return ([x if x < self.src_vocab_size else self.unk_id
                 for x in sentence_pair[0]],
                [x if x < self.trg_vocab_size else self.unk_id
                 for x in sentence_pair[1]],
                [x for x in sentence_pair[2]])
'''
class _oov_to_unk(object):
    """Maps out of vocabulary token index to unk token index."""
    def __init__(self, src_vocab_size=30000, trg_vocab_size=30000,
                 unk_id=1):
        self.src_vocab_size = src_vocab_size
        self.trg_vocab_size = trg_vocab_size
        self.unk_id = unk_id

    def __call__(self, sentence_pair):
        return ([x if x < self.src_vocab_size else self.unk_id
                 for x in sentence_pair[0]],
                [x if x < self.trg_vocab_size else self.unk_id
                 for x in sentence_pair[1]])
'''

class _too_long(object):
    """Filters sequences longer than given sequence length."""
    def __init__(self, seq_len=50):
        self.seq_len = seq_len

    def __call__(self, sentence_pair):
        return all([len(sentence) <= self.seq_len
                    for sentence in sentence_pair])


def get_tr_stream(src_vocab, trg_vocab, src_data, trg_data,
                  src_vocab_size=30000, trg_vocab_size=30000, unk_id=1,
                  seq_len=50, batch_size=80, sort_k_batches=12, **kwargs):
    """Prepares the training data stream."""

    # Load dictionaries and ensure special tokens exist
    src_vocab = _ensure_special_tokens(
        src_vocab if isinstance(src_vocab, dict)
        else cPickle.load(open(src_vocab, 'rb')),
        bos_idx=0, eos_idx=src_vocab_size - 1, unk_idx=unk_id)
    trg_vocab = _ensure_special_tokens(
        trg_vocab if isinstance(trg_vocab, dict) else
        cPickle.load(open(trg_vocab, 'rb')),
        bos_idx=0, eos_idx=trg_vocab_size - 1, unk_idx=unk_id)

    # Get text files from both source and target
    src_dataset = TextFile([src_data], src_vocab, None)
    trg_dataset = TextFile([trg_data], trg_vocab, None)

    # Merge them to get a source, target pair
    stream = Merge([src_dataset.get_example_stream(),
                    trg_dataset.get_example_stream()],
                   ('source', 'target'))

    # Filter sequences that are too long
    stream = Filter(stream,
                    predicate=_too_long(seq_len=seq_len))

    # Replace out of vocabulary tokens with unk token
    stream = Mapping(stream,
                     _oov_to_unk(src_vocab_size=src_vocab_size,
                                 trg_vocab_size=trg_vocab_size,
                                 unk_id=unk_id))

    # Build a batched version of stream to read k batches ahead
    stream = Batch(stream,
                   iteration_scheme=ConstantScheme(
                       batch_size*sort_k_batches))

    # Sort all samples in the read-ahead batch
    stream = Mapping(stream, SortMapping(_length))

    # Convert it into a stream again
    stream = Unpack(stream)

    # Construct batches from the stream with specified batch size
    stream = Batch(
        stream, iteration_scheme=ConstantScheme(batch_size))

    # Pad sequences that are short
    masked_stream = PaddingWithEOS(
        stream, [src_vocab_size - 1, trg_vocab_size - 1])

    return masked_stream

def get_tr_stream_with_topicalq(src_vocab, trg_vocab,topical_vocab, src_data, trg_data,topical_data,
                  src_vocab_size=30000, trg_vocab_size=30000,topical_vocab_size=2000, unk_id=1,
                  seq_len=50, batch_size=80, sort_k_batches=12, **kwargs):
    """Prepares the training data stream."""

    # Load dictionaries and ensure special tokens exist

    # src_vocab = _ensure_special_tokens(
    #     src_vocab if isinstance(src_vocab, dict)
    #     else cPickle.load(open(src_vocab, 'rb')),
    #     bos_idx=0, eos_idx=src_vocab_size - 1, unk_idx=unk_id)
    # trg_vocab = _ensure_special_tokens(
    #     trg_vocab if isinstance(trg_vocab, dict) else
    #     cPickle.load(open(trg_vocab, 'rb')),
    #     bos_idx=0, eos_idx=trg_vocab_size - 1, unk_idx=unk_id)
    src_data = '/home/research/qinghua_tu/TA-Seq2Seq/model/post_repos_sina4_thulac1_src'
    trg_data = '/home/research/qinghua_tu/TA-Seq2Seq/model/post_repos_sina4_thulac1_tgt'
    topical_data = '/home/research/qinghua_tu/TA-Seq2Seq/model/doc_topic.0_topic'
    src_vocab = cPickle.load(open('/home/research/qinghua_tu/TA-Seq2Seq/model/src_vocab.pkl', 'rb'))
    trg_vocab = src_vocab
    topical_vocab='/home/research/qinghua_tu/TA-Seq2Seq/model/topic_vocab.pkl'
    topical_vocab =cPickle.load(open(topical_vocab, 'rb'));#not ensure special token.

    # Get text files from both source and target
    src_dataset = TextFile([src_data], src_vocab, None)
    trg_dataset = TextFile([trg_data], trg_vocab, None)
    topical_dataset = TextFile([topical_data],topical_vocab,None,None);

    # Merge them to get a source, target pair
    stream = Merge([src_dataset.get_example_stream(),
                    trg_dataset.get_example_stream(),
                    topical_dataset.get_example_stream()],
                   ('source', 'target','source_topical'))


    # Filter sequences that are too long
    stream = Filter(stream,
                    predicate=_too_long(seq_len=seq_len))

    # Replace out of vocabulary tokens with unk token
    # The topical part are not contained of it, check~
    stream = Mapping(stream,
                     _oov_to_unk(src_vocab_size=src_vocab_size,
                                 trg_vocab_size=trg_vocab_size,
                                 topical_vocab_size=topical_vocab_size,
                                 unk_id=unk_id))

    # Build a batched version of stream to read k batches ahead
    stream = Batch(stream,
                   iteration_scheme=ConstantScheme(
                       batch_size*sort_k_batches))

    # Sort all samples in the read-ahead batch
    stream = Mapping(stream, SortMapping(_length))

    # Convert it into a stream again
    stream = Unpack(stream)

    # Construct batches from the stream with specified batch size
    stream = Batch(
        stream, iteration_scheme=ConstantScheme(batch_size))

    # Pad sequences that are short
    masked_stream = PaddingWithEOS(
        stream, [src_vocab_size - 1,trg_vocab_size - 1, topical_vocab_size - 1],mask_dtype="float32")

    return masked_stream



def get_tr_stream_single_score(src_vocab, src_data, trg_data,
                  src_vocab_size=30000, unk_id=1,
                  seq_len=50, batch_size=80, sort_k_batches=12, **kwargs):
    """Prepares the training data stream."""

    # Load dictionaries and ensure special tokens exist
    src_vocab = _ensure_special_tokens(
        src_vocab if isinstance(src_vocab, dict)
        else cPickle.load(open(src_vocab, 'rb')),
        bos_idx=0, eos_idx=src_vocab_size - 1, unk_idx=unk_id)

    # Get text files from both source and target
    src_dataset = TextFile([src_data], src_vocab, None)
    #trg_dataset = TextFile([trg_data], trg_vocab, None) should check the src_dataset's dimension.

    # Merge them to get a source, target pair
    stream = Merge([src_dataset.get_example_stream(),
                    trg_dataset.get_example_stream()],
                   ('source', 'target'))

    # Filter sequences that are too long
    stream = Filter(stream,
                    predicate=_too_long(seq_len=seq_len))

    # Replace out of vocabulary tokens with unk token
    stream = Mapping(stream,
                     _oov_to_unk(src_vocab_size=src_vocab_size,
                                 unk_id=unk_id))

    # Build a batched version of stream to read k batches ahead
    stream = Batch(stream,
                   iteration_scheme=ConstantScheme(
                       batch_size*sort_k_batches))

    # Sort all samples in the read-ahead batch
    stream = Mapping(stream, SortMapping(_length))

    # Convert it into a stream again
    stream = Unpack(stream)

    # Construct batches from the stream with specified batch size
    stream = Batch(
        stream, iteration_scheme=ConstantScheme(batch_size))

    # Pad sequences that are short
    masked_stream = PaddingWithEOS(
        stream, src_vocab_size - 1)

    return masked_stream

def get_dev_stream_with_topicalq(test_set=None, src_vocab=None, src_vocab_size=30000,topical_test_set=None,topical_vocab=None,topical_vocab_size=2000,
                   unk_id=1, **kwargs):
    """Setup development set stream if necessary."""
    dev_stream = None
    test_set='/home/research/qinghua_tu/TA-Seq2Seq/model/test_src'
    if test_set is not None and src_vocab is not None:
        # src_vocab = _ensure_special_tokens(
        #     src_vocab if isinstance(src_vocab, dict) else
        #     cPickle.load(open(src_vocab, 'rb')),
        #     bos_idx=0, eos_idx=src_vocab_size - 1, unk_idx=unk_id)
        # print test_set, type(src_vocab)
        src_vocab = cPickle.load(open('/home/research/qinghua_tu/TA-Seq2Seq/model/src_vocab.pkl', 'rb'))

        topical_vocab = '/home/research/qinghua_tu/TA-Seq2Seq/model/topic_vocab.pkl'
        topical_test_set='/home/research/qinghua_tu/TA-Seq2Seq/model/test_src_topic100'
        topical_vocab =cPickle.load(open(topical_vocab, 'rb'));#not ensure special token.
        topical_dataset = TextFile([topical_test_set],topical_vocab,None,None)
        dev_dataset = TextFile([test_set], src_vocab, None)
        #dev_stream = DataStream(dev_dataset)
        # Merge them to get a source, target pair
        dev_stream = Merge([dev_dataset.get_example_stream(),
                            topical_dataset.get_example_stream()],
                            ('source','source_topical'))
    return dev_stream

def get_tr_stream_unsorted(src_vocab, trg_vocab, src_data, trg_data,
                  src_vocab_size=30000, trg_vocab_size=30000, unk_id=1,
                  seq_len=50, batch_size=80, sort_k_batches=12, **kwargs):
    """Prepares the training data stream."""

    # Load dictionaries and ensure special tokens exist
    src_vocab = _ensure_special_tokens(
        src_vocab if isinstance(src_vocab, dict)
        else cPickle.load(open(src_vocab, 'rb')),
        bos_idx=0, eos_idx=src_vocab_size - 1, unk_idx=unk_id)
    trg_vocab = _ensure_special_tokens(
        trg_vocab if isinstance(trg_vocab, dict) else
        cPickle.load(open(trg_vocab, 'rb')),
        bos_idx=0, eos_idx=trg_vocab_size - 1, unk_idx=unk_id)

    # Get text files from both source and target
    src_dataset = TextFile([src_data], src_vocab, None)
    trg_dataset = TextFile([trg_data], trg_vocab, None)

    # Merge them to get a source, target pair
    stream = Merge([src_dataset.get_example_stream(),
                    trg_dataset.get_example_stream()],
                   ('source', 'target'))

    # Replace out of vocabulary tokens with unk token
    stream = Mapping(stream,
                     _oov_to_unk(src_vocab_size=src_vocab_size,
                                 trg_vocab_size=trg_vocab_size,
                                 unk_id=unk_id))


    # Construct batches from the stream with specified batch size
    stream = Batch(
        stream, iteration_scheme=ConstantScheme(batch_size))

    # Pad sequences that are short
    masked_stream = PaddingWithEOS(
        stream, [src_vocab_size - 1, trg_vocab_size - 1])

    return masked_stream