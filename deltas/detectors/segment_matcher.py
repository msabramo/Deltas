"""
Match segments
--------------

Performs a diffs using a tree of matchable segments in order to remain robust
to content moves.  This module supports the use of a custom
:class:`~deltas.segmenters.Segmenter`.

:Example:
    >>> from deltas import segment_matcher, apply
    >>>
    >>> a_tokens = ["This", " ", "comes", " ", "first", ".",
    ...             " ",
    ...             "This", " ", "comes", " ", "second", "."]
    >>>
    >>> b_tokens = ["This", " ", "comes", " ", "second", ".",
    ...             " ",
    ...             "This", " ", "comes", " ", "first", "."]
    >>>
    >>> operations = segment_matcher.diff(a_tokens, b_tokens)
    >>>
    >>> for operation in operations:
    ...     print(operation)
    ...
    Equal(name='equal', a1=7, a2=13, b1=0, b2=6)
    Insert(name='insert', a1=6, a2=7, b1=6, b2=7)
    Equal(name='equal', a1=0, a2=6, b1=7, b2=13)
    Delete(name='delete', a1=6, a2=7, b1=13, b2=13)

"""
from collections import defaultdict

from . import sequence_matcher
from ..operations import Delete, Equal, Insert
from ..segmenters import (MatchableSegment, MatchableSegmentNode,
                          ParagraphsSentencesAndWhitespace, Segmenter,
                          SegmentNodeCollection, Token)
from .detector import Detector

SEGMENTER = ParagraphsSentencesAndWhitespace()

class SegmentMatcher(Detector):
    """
    Constructs a fully configured SegmentMatcher detector.
    """
    def __init__(self, segmenter=None):
        segmenter = segmenter or SEGMENTER
        
        if hasattr(segmenter, "segment"):
            self.segmenter = segmenter
        else:
            raise TypeError("Expected {0}, ".format(Segmenter) +
                            "but got {0}.".format(segmenter))
            
        
    def diff(self, a, b):
        return diff(a, b, segmenter=self.segmenter)
    
    @classmethod
    def from_config(cls, doc, name):
        
        segmenter = \
                Segmenter.from_config(doc, doc['detectors'][name]['segmenter'])
        
        return cls(segmenter=segmenter)

def diff(a, b, segmenter=None):
    """
    Performs a longest common substring diff.
    
    :Parameters:
        a : sequence of `comparable`
            Initial sequence
        b : sequence of `comparable`
            Changed sequence
        segmenter : :class:`~deltas.Segmenter`
            A segmenter to use on the tokens.
        
    :Returns:
        An `iterable` of operations.
    """
    segmenter = segmenter or SEGMENTER
    
    # Cluster the input tokens
    a_segments = segmenter.segment(a)
    b_segments = segmenter.segment(b)
    
    return diff_segments(a_segments, b_segments)

def diff_segments(a_segments, b_segments):
    
    # Match and re-sequence unmatched tokens
    a_segment_tokens, b_segment_tokens = _cluster_matching_segments(a_segments,
                                                                    b_segments)
    
    # Perform a simple LCS over unmatched tokens and clusters
    clustered_ops = sequence_matcher.diff(a_segment_tokens, b_segment_tokens)
    
    # Return the expanded (de-clustered) operations
    return _expand_clustered_ops(clustered_ops,
                                 a_segment_tokens,
                                 b_segment_tokens)

def _build_segment_map(segments):
    segment_map = defaultdict(lambda:[])
    for segment in segments:
        if isinstance(segment, MatchableSegment):
            
            segment_map[segment].append(segment)
           
            if isinstance(segment, SegmentNodeCollection):
                # If the children are not tokens
                for subsegments in _build_segment_map(segment).values():
                    segment_map[subsegments[0]].extend(subsegments)
    
    return segment_map


def _match_segments(a_segment_map, b_segments):
    for segment in b_segments:
        if isinstance(segment, MatchableSegment) and segment in a_segment_map:
            matched_segments = a_segment_map[segment] # Get matches
            for matched_segment in matched_segments: # For each match
                matched_segment.match = segment # flag as matched
            segment.match = matched_segments[0] # Always associate with first match
            yield segment # Dump matched segment
            
        elif isinstance(segment, SegmentNodeCollection):
            for n in _match_segments(a_segment_map, segment): yield n # Recurse
            
        else:
            for t in segment: yield t # Dump tokens
        
    
def _expand_unpatched_segments(a_segments):
    for segment in a_segments:
        # Check if a segment is matched.
        if isinstance(segment, MatchableSegment) and segment.match is not None:
            yield segment # Yield matched segment as cluster
        elif isinstance(segment, SegmentNodeCollection):
            for s in _expand_unpatched_segments(segment): yield s # Recurse
        else:
            for t in segment: yield t # Dump unmatched tokens

def _cluster_matching_segments(a_segments, b_segments):
    
    # Generate a look-up map for matchable segments in 'a'
    a_segment_map = _build_segment_map(a_segments)
    
    # Find and cluster matching content in 'b'
    b_segment_tokens = list(_match_segments(a_segment_map, b_segments))
    
    # Expand unmatched segments from 'a'
    a_segment_tokens = list(_expand_unpatched_segments(a_segments))
    
    return a_segment_tokens, b_segment_tokens

def _expand_clustered_ops(operations, a_token_clusters, b_token_clusters):
    
    position = 0
    for operation in operations:
        if isinstance(operation, Equal):
            #print("Processing equal:")
            new_ops = _process_equal(position, operation,
                                     a_token_clusters, b_token_clusters)
            
        elif isinstance(operation, Insert):
            #print("Processing insert:")
            new_ops = _process_insert(position, operation,
                                      a_token_clusters, b_token_clusters)
            
        elif isinstance(operation, Delete):
            #print("Processing remove:")
            new_ops = _process_delete(position, operation,
                                      a_token_clusters, b_token_clusters)
            
        else:
            assert False, "Should never happen"
        
        for new_op in new_ops:
            
            yield new_op
            position = position + (new_op.b2 - new_op.b1)
            

def _process_equal(position, operation, a_token_clusters, b_token_clusters):
    yield Equal(a_token_clusters[operation.a1].start,
                a_token_clusters[operation.a2-1].end,
                b_token_clusters[operation.b1].start,
                b_token_clusters[operation.b2-1].end)

def _process_insert(position, operation, a_token_clusters, b_token_clusters):
    
    inserted_tokens = []
    for token_or_segment in b_token_clusters[operation.b1:operation.b2]:
        
        if isinstance(token_or_segment, Token):
            inserted_tokens.append(token_or_segment)
        else: # Found a matched token.
            if len(inserted_tokens) > 0:
                yield Insert(inserted_tokens[0].start,
                             inserted_tokens[-1].end,
                             position,
                             position+len(inserted_tokens))
                
                # update & reset!
                position += len(inserted_tokens)
                inserted_tokens = []
            
            match = token_or_segment.match
            yield Equal(match.start, match.end,
                        position, position+(match.end-match.start))
            
            # update!
            position += match.end-match.start
                
        
    
    # cleanup
    if len(inserted_tokens) > 0:
        yield Insert(inserted_tokens[0].start,
                     inserted_tokens[-1].end,
                     position,
                     position+len(inserted_tokens))

def _process_delete(position, operation, a_token_clusters, b_token_clusters):
    removed_tokens = []
    for token_or_segment in a_token_clusters[operation.a1:operation.a2]:
        
        if isinstance(token_or_segment, Token):
            removed_tokens.append(token_or_segment)
        else: # Found a matched token... not removed -- just moved
            if len(removed_tokens) > 0:
                yield Delete(removed_tokens[0].start,
                             removed_tokens[-1].end,
                             position,
                             position)
            
            # update & reset!
            position += len(removed_tokens)
            removed_tokens = []
        
    # cleanup
    if len(removed_tokens) > 0:
        yield Delete(removed_tokens[0].start,
                     removed_tokens[-1].end,
                     position,
                     position)
