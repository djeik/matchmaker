#!/usr/bin/env python

from __future__ import print_function

import ibd
import bed
import jerrington_tools as jt
import dataset_utils

from itertools import imap, ifilter, chain
from operator import eq

ANCESTRY_DATA_CACHE = {}
def CLEAR_ANCESTRY_CACHE():
    ANCESTRY_DATA_CACHE = {}

class IBDAncestryMatch:
    """ For some pair of individuals, this class represents the product of
        their shared ancestry and their IBD segment. This class supports rich
        comparison, where instances are compared on the basis of the length of
        the IBD segment.
        """

    @staticmethod
    def from_ibds_and_bedpath(handles, outbed_path, ibd_filterf,
            robust=False, debug_mode=False):
        """ Construct a generator to yield a list of IBDAncestryMatch objects
            from the given directory of ancestry data and list of handles.
            An optional filter can be provided to restrict which entries are
            read in from the ibd file. All operations are performed with
            generators, which should result in overall improved data fusion.
            """
        def get_chromosome_data(handles, filterf):
            """ Construct a generator to yield all the IBD entries for the
                African-American HRS dataset. We are pulling Soheil's MergedData
                dataset, so the GERMLINE output files need to be filtered to remove
                entries from the other datasets.

                Arguments:
                    handles (list of file handles):
                        Handles on all the files to load.
                """
            return ifilter(filterf, chain(*map(ibd.IBDEntry.ifrom_GERMLINE,
                handles)))

        id_to_bedfile = lambda i, h: "".join(["T", i, "_", h, "_cM.bed"])

        # a utility function
        flipcurry2 = jt.compose(jt.curry2, jt.flip)

        # construct a function that takes an IBDEntry and generates the match
        # object from it.
        match_from_ibd_segment__ = IBDAncestryMatch.from_ibd_segment
        my_from_ibd_segment = jt.supply(match_from_ibd_segment__,
                {"generate":True, "cache":True, "robust":robust,
                    "filename_parserf":dataset_utils.sccs_name_parser})
        match_from_ibd_segment = flipcurry2(my_from_ibd_segment)(outbed_path)

        # compute the ancestry matches for those individuals
        matches = ifilter(lambda x: x.length() > 0, imap(
            match_from_ibd_segment,
            get_chromosome_data(handles, dataset_utils.is_sccs)))

        return matches

    @staticmethod
    def from_ibd_segment(ibd_segment, bed_dir, cache=True, generate=False,
            robust=False, filename_parserf=None):
        """ Construct an IBDAncestryMatch from only an IBD segment and a
            repository of bed files, optionally generating the shared ancestry
            segment, and optionally checking for robustness.
            In other words, this factory method will do the work of loading the
            necessary bed files, using the default name decoration procedure
            (see bed.Individual) from the given directory.

            Arguments:
                ibd_segment (ibd.IBDEntry):
                    The segment from which the names of the related individuals
                    are used to load the relevant local ancestry data.
                bed_dir (path):
                    The path to the directory in which reside all the bed
                    files.
                cache (boolean) (default: True):
                    Whether to cache the loaded ancestry files for reuse,
                    should a request for an individual's ancestry occur more
                    than once. Otherwise, the ancestry is reloaded from disk,
                    which will cause poor memory use.
                generate (boolean) (default: False):
                    Whether to call the `compute` method on the newly-created
                    IBDAncestryMatch instance, which would generate the shared
                    ancestry segment.
                robust (boolean) (default: False):
                    Whether to perform robustness checking during the
                    computation of the shared ancestry segment. The value of
                    this parameter is irrelevant if `generate` is False.

            Returns (IBDAncestryMatch):
                The constructed value, with the shared ancestry segment having
                been computed if `generate` was set to True.

            Note:
                If in the dataset being used, the same individual is IBD with
                more than one other individual, then this factory method will
                load the bed file more than once, which will result in poor
                memory performance. For better performance, it is advisable to
                set the `cache` parameter to True, which will cause
                newly-loaded ancestry data to be cached, and recalled later if
                necessary.
                The cache is especially useful since IBD information is saved
                chromosome-wise, but ancestry data is stored individual-wise.
                So when we're processing by chromosome, according to IBD
                information for example, we only need information for
                chromosome 1, at the beginning of the file, but we load the
                whole file. So rather than reload the same individual 22 times
                in total, caching allows us to save on space, overall.
            """
        load_from_disk = jt.curry2(
                jt.supply(
                    bed.Individual.from_dir_and_name,
                    {"parserf":filename_parserf}))(bed_dir)

        def load_individual(name):
            """ Function that will first try to load an individual from the
                cache, and if that fails, load from disk, saving the loaded
                individual in the cache. If the cache parameter is false, then
                this function will always load from disk.
                """
            if not cache:
                return load_from_disk(name)

            try:
                return ANCESTRY_DATA_CACHE[name]
            except KeyError:
                i = load_from_disk(name)
                ANCESTRY_DATA_CACHE[name] = i
                return i

        individuals = map(load_individual,
                ibd_segment.name)
        match = IBDAncestryMatch(ibd_segment, individuals)
        if generate:
            match.compute(robust=robust)
        return match

    @staticmethod
    def generate(ibd_segment, individuals, robust=False):
        """ Factory method to construct an IBDAncestryMatch and immediately
            perform the shared ancestry determination, with optional
            robustness checking.
            The arguments are as given to `__init__` and `compute`, so it is
            advised to consult the documentation on those methods.
            """
        match = IBDAncestryMatch(ibd_segment, individuals)
        match.compute(robust=robust)
        return match

    def __init__(self, ibd_segment, individuals, shared_segment=None):
        """ Constructor.

            Arguments:
                ibd_segment (ibd.IBDEntry):
                    the identity by descent (IBD) segment, as loaded from
                    (optionally filtered) GERMLINE output.
                individuals (list of bed.Individual):
                    the individuals related in this IBD segment.
                    Warning: the order in which the individuals is given in the
                    argument list must match the order in which their names
                    appear in the IBD entry.
                shared_segment (jerrington_tools.Interval) (default: None):
                    the interval over which the individuals have a common
                    ancestry.

            Note:
                The constructor does not automatically generate the
                shared_segment member. To generate it, call the `compute`
                method on an instance, or use the static factory method
                `generate`, which will construct the instance and immediately
                call compute on it.
            """

        self.ibd_segment = ibd_segment
        self.individuals = individuals
        self.chromosome  = ibd_segment.chromosome

        # TODO increase robustness by automatically switching the individuals
        # if it is determined that the IBD order is the opposite of the given
        # order
        if not all(imap(eq,
                jt.for_each(self.individuals, jt.project_c("name")),
                self.ibd_segment.name)):
            raise ValueError("the given IBD segment does not relate the given "
                    "individuals.")

    def compute(self, robust=False):
        """ Perform shared ancestry determination, with optional robustness
            checking. The resulting list of AncestrySegment objects which are
            shared are stored in the shared_segments attribute of this
            instance.

            Argument:
                robust (boolean) (default: False):
                    Verify that the shared segment determination satisfies the
                    commutativity law by performing the determination both
                    ways and checking for the equality of the determined
                    segments. This effectively doubles the runtime of this
                    method, however.

            Returns (boolean):
                Whether the determined segment is nonempty.
            """
        shared_segments = self.individuals[0].shared_ancestry_with(
                self.individuals[1],
                bed.Individual.bed_code_from_IBD(
                    self.ibd_segment.haplotype[0]),
                bed.Individual.bed_code_from_IBD(
                    self.ibd_segment.haplotype[1]),
                self.ibd_segment.chromosome,
                self.ibd_segment.type)

        if robust:
            shared_segments_reverse = self.individuals[1].shared_ancestry_with(
                    self.individuals[0],
                    bed.Individual.bed_code_from_IBD(
                        self.ibd_segment.haplotype[1]),
                    bed.Individual.bed_code_from_IBD(
                        self.ibd_segment.haplotype[0]),
                    self.ibd_segment.chromosome,
                    self.ibd_segment.type)

            if shared_segments != shared_segments_reverse:
                raise ValueError("Robustness check failed for individuals",
                        self.individuals[0].name, "and",
                        self.individuals[1].name)

        self.shared_segments = shared_segments

        return not self.is_empty()

    def calculate_ibd_ancestry_sizes(self):
        """ Get a dict associating ancestry codenames with their size within
            the IBD region of this match. Sizes are whatever type is used for
            the IBDEntry. The constructed list is saved to the attribute
            ibd_ancestry_sizes, and is also returned.
            """
        if self.ibd_segment.type == "MB":
            getinterval = jt.project_c("interval_bp")
        else:
            getinterval = jt.project_c("interval_cm")

        ibd_interval = self.ibd_segment.interval
        haplotype_codes = map(
                bed.Individual.bed_code_from_IBD,
                self.ibd_segment.haplotype)
        chrom = (self.individuals[0]
                                 [haplotype_codes[0]]
                                 [self.ibd_segment.chromosome - 1])
        sizes = {}
        for h in bed.AncestryCode.CODENAMES:
            sizes[h] = 0 # initialize each ancestry to zero

        for segment in chrom.segments:
            sizes[segment.code.name] += \
                    ibd_interval.intersection(getinterval(segment)).length()

        self.ibd_ancestry_sizes = sizes
        return sizes

    def is_empty(self):
        """ Whether the match is empty. A match is empty if there are no match
            segments. """
        return len(self.shared_segments) == 0

    def length(self):
        """ Gets the total length of the shared segments, ignoring gaps between
            them, if any.
            An exception is raised if the shared segments have not been computed
            yet.
            Zero is returned if there are no shared segments.
            """
        type = self.ibd_segment.type
        if type == "MB":
            def get_interval(s):
                return s.interval_bp
        elif type == "cM":
            def get_interval(s):
                return s.interval_cm
        else:
            raise ValueError("invalid type of length to get.")

        if self.shared_segments is None:
            raise ValueError("Cannot get length of unevaluated match.")

        elif not self.shared_segments:
            r = 0
        else:
            r = (get_interval(self.shared_segments[-1]).end -
                    get_interval(self.shared_segments[0]).start)
        return r

    def to_string(self):
        """ Render this match object as a string of GERMLINE output combined
            with the relevant fields from the associated local ancestry data.
            The resulting string can be loaded with the from_string factory
            method.
            """
        ibd_string = self.ibd_segment.to_string()
        rfmix_strings = []

        chromosome_number = self.ibd_segment.chromosome

        for (i, individual) in enumerate(self.individuals):
            hap_code = bed.Individual.bed_code_from_IBD(
                    self.ibd_segment.haplotype[i])
            chromosome = individual[hap_code][chromosome_number - 1]
            individual_strings = ["<#>"]
            for segment in chromosome.segments:
                individual_strings.append(segment.to_string())
            rfmix_strings.append('\t'.join(individual_strings))

        return "\t".join([ibd_string] + rfmix_strings)

    def __lt__(self, other):
        """ Compare this IBDAncestryMatch with another on the basis of their
            IBD segments. IBD segments are compared on the basis of their
            lengths.
            Therefore, the default ordering for IBDAncestryMatch instances is
            from smallest to largest.
            """
        return self.ibd_segment < other.ibd_segment

    def __gt__(self, other):
        """ Compare this IBDAncestryMatch with another on the basis of their
            IBD segments. IBD segments are compared on the basis of their
            lengths.
            Therefore, the default ordering for IBDAncestryMatch instances is
            from smallest to largest.
            """
        return self.ibd_segment > other.ibd_segment

    def __repr__(self):
        return "IBDAncestryMatch(%s, %s, %s)" % (
                repr(self.ibd_segment), repr(self.individuals),
                repr(self.shared_segment))
