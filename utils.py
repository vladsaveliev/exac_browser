import os
import re
from operator import itemgetter
import json

import sys
from bson import ObjectId

AF_BUCKETS = [0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1]
METRICS = [
    'BaseQRankSum',
    'ClippingRankSum',
    'DP',
    'FS',
    'InbreedingCoeff',
    'MQ',
    'MQRankSum',
    'QD',
    'ReadPosRankSum',
    'VQSLOD'
]

default_filt_params = {
    'min_af': 0.075,
    'act_min_af': 0.025
}

EXAC_FILES_DIRECTORY = '../exac_data/'
KEY_GENES_FPATH = os.path.join(os.path.dirname(__file__), EXAC_FILES_DIRECTORY, 'az_key_genes.823.txt')


class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        return json.JSONEncoder.default(self, o)


def get_projects(full_db):
    projects = full_db.projects.find()
    projects = [(project['name'], project['genome']) for project in projects]
    return projects


def get_one_project(full_db, project_name, genome):
    if not genome:
        print('Error! Please specify project name and genome')
        sys.exit(2)
    project = (project_name, genome)
    if not full_db.projects.find({'name': project_name, 'genome': genome}):
        full_db.projects.insert({'name': project_name, 'genome': genome})
    return project


def get_project_key(project_name, genome):
    return project_name + '_' + genome


def get_project_by_project_name(db, project_name):
    projects = list(db.projects.find({'name': project_name}))
    return projects[0] if projects else None


def get_project_samples(db, project_name, genome):
    samples = list(db[get_project_key(project_name, genome)].samples.find())
    if not samples:
        return None
    sample_names = sorted([sample['name'] for sample in samples], key=natural_key)
    return sample_names


def get_sample_key(db, sample_name, project_name, genome):
    samples = list(db[get_project_key(project_name, genome)].samples.find())
    for sample in samples:
        if sample['name'] == sample_name and 'idx' in sample:
            return sample['idx']
    return sample_name


def add_transcript_coordinate_to_variants(db, genome, variant_list, transcript_id):
    """
    Each variant has a 'xpos' and 'pos' positional attributes.
    This method takes a list of variants and adds a third position: the "transcript coordinates".
    This is defined as the distance from the start of the transcript, in coding bases.
    So a variant in the 7th base of the 6th exon of a transcript will have a transcript coordinate of
    the sum of the size of the first 5 exons) + 7
    This is 0-based, so a variant in the first base of the first exon has a transcript coordinate of 0.

    You may want to add transcript coordinates for multiple transcripts, so this is stored in a variant as
    variant['transcript_coordinates'][transcript_id]

    If a variant in variant_list does not have a `transcript_coordinates` dictionary, we create one

    If a variant start position for some reason does not fall in any exons in this transcript, its coordinate is 0.
    This is perhaps logically inconsistent,
    but it allows you to spot errors quickly if there's a pileup at the first base.
    `None` would just break things.

    Consider the behavior if a 20 base deletion deletes parts of two exons.
    I think the behavior in this method is consistent, but beware that it might break things downstream.

    Edits variant_list in place; no return val
    """

    import lookups
    # make sure exons is sorted by (start, end)
    exons = sorted(lookups.get_exons_in_transcript(db, genome, transcript_id), key=itemgetter('start', 'stop'))

    # offset from start of base for exon in ith position (so first item in this list is always 0)
    exon_offsets = [0 for i in range(len(exons))]
    for i, exon in enumerate(exons):
        for j in range(i+1, len(exons)):
            exon_offsets[j] += exon['stop'] - exon['start']

    for variant in variant_list:
        if 'transcript_coordinates' not in variant:
            variant['transcript_coordinates'] = {}
        variant['transcript_coordinates'][transcript_id] = 0
        for i, exon in enumerate(exons):
            if exon['start'] <= variant['pos'] <= exon['stop']:
                variant['transcript_coordinates'][transcript_id] = exon_offsets[i] + variant['pos'] - exon['start']


def xpos_to_pos(xpos):
    return int(xpos % 1e9)


def add_consequence_to_variants(variant_list):
    for variant in variant_list:
        add_consequence_to_variant(variant)


def add_consequence_to_variant(variant):
    worst_csq = worst_csq_with_vep(variant['vep_annotations'])
    if worst_csq is None: return
    variant['major_consequence'] = worst_csq['major_consequence']
    variant['HGVSp'] = get_protein_hgvs(worst_csq)
    variant['HGVSc'] = get_transcript_hgvs(worst_csq)
    variant['HGVS'] = get_proper_hgvs(worst_csq)
    variant['CANONICAL'] = worst_csq['CANONICAL']
    variant['gene'] = worst_csq['Gene_Name']
    variant['indel'] = len(variant['ref']) != len(variant['alt'])
    if csq_order_dict[variant['major_consequence']] <= csq_order_dict["frameshift_variant"]:
        variant['category'] = 'lof_variant'
        for annotation in variant['vep_annotations']:
            if annotation['LoF'] == '':
                annotation['LoF'] = 'NC'
                annotation['LoF_filter'] = 'Non-protein-coding gene'
    elif csq_order_dict[variant['major_consequence']] <= csq_order_dict["missense_variant"]:
        # Should be noted that this grabs inframe deletion, etc.
        variant['category'] = 'missense_variant'
    elif csq_order_dict[variant['major_consequence']] <= csq_order_dict["synonymous_variant"]:
        variant['category'] = 'synonymous_variant'
    else:
        variant['category'] = 'other_variant'
    variant['flags'] = get_flags_from_variant(variant)


def get_flags_from_variant(variant):
    flags = []
    if 'mnps' in variant:
        flags.append('MNP')
    lof_annotations = [x for x in variant['vep_annotations'] if x['LoF'] != '']
    if not len(lof_annotations): return flags
    if all(['LoF' in x and x['LoF'] != 'HC' for x in lof_annotations]):
        flags.append('LC LoF')
    if all(['LoF_flags' in x and x['LoF_flags'] != '' for x in lof_annotations]):
        flags.append('LoF flag')
    return flags


protein_letters_1to3 = {
    'A': 'Ala', 'C': 'Cys', 'D': 'Asp', 'E': 'Glu',
    'F': 'Phe', 'G': 'Gly', 'H': 'His', 'I': 'Ile',
    'K': 'Lys', 'L': 'Leu', 'M': 'Met', 'N': 'Asn',
    'P': 'Pro', 'Q': 'Gln', 'R': 'Arg', 'S': 'Ser',
    'T': 'Thr', 'V': 'Val', 'W': 'Trp', 'Y': 'Tyr',
    'X': 'Ter', '*': 'Ter', 'U': 'Sec'
}


def get_proper_hgvs(csq):
    # Needs major_consequence
    if csq['major_consequence'] in ('splice_donor_variant', 'splice_acceptor_variant', 'splice_region_variant'):
        return get_transcript_hgvs(csq)
    else:
        return get_protein_hgvs(csq)


def get_transcript_hgvs(csq):
    return csq['HGVS_c'].split(':')[-1]


def get_protein_hgvs(annotation):
    """
    Takes consequence dictionary, returns proper variant formatting for synonymous variants
    """
    if '%3D' in annotation['HGVS_p']: # "%3D" is "="
        try:
            amino_acids = ''.join([protein_letters_1to3[x] for x in annotation['Amino_acids']])
            return "p." + amino_acids + annotation['Protein_position'] + amino_acids
        except Exception as e:
            print('Could not create HGVS for: %s' % annotation)
    return annotation['HGVS_p'].split(':')[-1]

# Note that this is the current as of v81 with some included for backwards compatibility (VEP <= 75)
csq_order = ["transcript_ablation",
"splice_acceptor_variant",
"splice_donor_variant",
"stop_gained",
"frameshift_variant",
"stop_lost",
"start_lost",  # new in v81
"initiator_codon_variant",  # deprecated
"transcript_amplification",
"disruptive_inframe_insertion",
"disruptive_inframe_deletion",
"inframe_insertion",
"inframe_deletion",
"conservative_inframe_insertion",
"conservative_inframe_deletion",
"missense_variant",
"protein_altering_variant",  # new in v79
"splice_region_variant",
"incomplete_terminal_codon_variant",
"stop_retained_variant",
"synonymous_variant",
"coding_sequence_variant",
"mature_miRNA_variant",
"5_prime_UTR_variant",
"5_prime_UTR_premature_start_codon_gain_variant",
"3_prime_UTR_variant",
"non_coding_transcript_exon_variant",
"non_coding_exon_variant",  # deprecated
"sequence_feature",
"intron_variant",
"NMD_transcript_variant",
"non_coding_transcript_variant",
"nc_transcript_variant",  # deprecated
"upstream_gene_variant",
"downstream_gene_variant",
"TFBS_ablation",
"TFBS_amplification",
"TF_binding_site_variant",
"regulatory_region_ablation",
"regulatory_region_amplification",
"feature_elongation",
"regulatory_region_variant",
"feature_truncation",
"intergenic_variant",
"protein_protein_contact"
""]
assert len(csq_order) == len(set(csq_order)) # No dupes!

csq_order_dict = dict((csq, i) for i,csq in enumerate(csq_order))
rev_csq_order_dict = dict(enumerate(csq_order))
assert all(csq == rev_csq_order_dict[csq_order_dict[csq]] for csq in csq_order)


def remove_extraneous_vep_annotations(annotation_list):
    return [ann for ann in annotation_list if worst_csq_index(ann['Annotation'].split('&')) <= csq_order_dict['intron_variant']]


def worst_csq_index(csq_list):
    """
    Input list of consequences (e.g. ['frameshift_variant', 'missense_variant'])
    Return index of the worst consequence (In this case, index of 'frameshift_variant', so 4)
    Works well with worst_csq_index('non_coding_exon_variant&nc_transcript_variant'.split('&'))
    """
    return min([csq_order_dict[csq] for csq in csq_list])


def worst_csq_from_list(csq_list):
    """
    Input list of consequences (e.g. ['frameshift_variant', 'missense_variant'])
    Return the worst consequence (In this case, 'frameshift_variant')
    Works well with worst_csq_from_list('non_coding_exon_variant&nc_transcript_variant'.split('&'))
    """
    return rev_csq_order_dict[worst_csq_index(csq_list)]


def worst_csq_from_csq(csq):
    """
    Input possibly &-filled csq string (e.g. 'non_coding_exon_variant&nc_transcript_variant')
    Return the worst consequence (In this case, 'non_coding_exon_variant')
    """
    return rev_csq_order_dict[worst_csq_index(csq.split('&'))]


def order_vep_by_csq(annotation_list):
    """
    Adds "major_consequence" to each annotation.
    Returns them ordered from most deleterious to least.
    """
    for ann in annotation_list:
        ann['major_consequence'] = worst_csq_from_csq(ann['Annotation'])
    return sorted(annotation_list, key=(lambda ann:csq_order_dict[ann['major_consequence']]))


def worst_csq_with_vep(annotation_list):
    """
    Takes list of VEP annotations [{'Annotation': 'frameshift', Feature: 'ENST'}, ...]
    Returns most severe annotation (as full VEP annotation [{'Annotation': 'frameshift', Feature: 'ENST'}])
    Also tacks on "major_consequence" for that annotation (i.e. worst_csq_from_csq)
    """
    if len(annotation_list) == 0:
        return None
    worst = max(annotation_list, key=annotation_severity)
    worst['major_consequence'] = worst_csq_from_csq(worst['Annotation'])
    return worst


def annotation_severity(annotation):
    "Bigger is more important."
    rv = -csq_order_dict[worst_csq_from_csq(annotation['Annotation'])]
    if annotation['CANONICAL'] == 'YES':
        rv += 0.1
    return rv

CHROMOSOMES = ['chr%s' % x for x in range(1, 23)]
CHROMOSOMES.extend(['chrX', 'chrY', 'chrM'])
CHROMOSOME_TO_CODE = dict((item, i + 1) for i, item in enumerate(CHROMOSOMES))


def get_single_location(chrom, pos):
    """
    Gets a single location from chromosome and position
    chr must be actual chromosme code (chrY) and pos must be integer

    Borrowed from xbrowse
    """
    if chrom in CHROMOSOME_TO_CODE:
        return CHROMOSOME_TO_CODE[chrom] * int(1e9) + pos


def get_xpos(chrom, pos):
    """
    Borrowed from xbrowse
    """
    if not chrom.startswith('chr'):
        chrom = 'chr{}'.format(chrom)
    return get_single_location(chrom, int(pos))


def get_minimal_representation(pos, ref, alt):
    """
    Get the minimal representation of a variant, based on the ref + alt alleles in a VCF
    This is used to make sure that multiallelic variants in different datasets,
    with different combinations of alternate alleles, can always be matched directly.

    Note that chromosome is ignored here - in xbrowse, we'll probably be dealing with 1D coordinates
    Args:
        pos (int): genomic position in a chromosome (1-based)
        ref (str): ref allele string
        alt (str): alt allele string
    Returns:
        tuple: (pos, ref, alt) of remapped coordinate
    """
    pos = int(pos)
    # If it's a simple SNV, don't remap anything
    if len(ref) == 1 and len(alt) == 1:
        return pos, ref, alt
    else:
        # strip off identical suffixes
        while(alt[-1] == ref[-1] and min(len(alt),len(ref)) > 1):
            alt = alt[:-1]
            ref = ref[:-1]
        # strip off identical prefixes and increment position
        while(alt[0] == ref[0] and min(len(alt),len(ref)) > 1):
            alt = alt[1:]
            ref = ref[1:]
            pos += 1
        return pos, ref, alt


def get_key_genes():
    key_genes = set()
    with open(KEY_GENES_FPATH) as f:
        for line in f:
            key_genes.add(line.strip())
    return key_genes


def set_key_genes(records):
    key_genes = get_key_genes()

    for record in records:
        record['is_key_gene'] = record['gene'] in key_genes


def format_value(value, unit='', human_readable=True, is_html=False):
    if value is None:
        return ''

    unit_str = unit
    if unit and is_html:
        unit_str = '<span class=\'rhs\'>&nbsp;</span>' + unit

    if isinstance(value, basestring):
        if human_readable:
            return '{value}{unit_str}'.format(**locals())
        else:
            return value

    elif isinstance(value, int):
        if value == 0:
            return '0'
        if human_readable:
            if value <= 9999:
                return str(value)
            else:
                v = '{value:,}{unit_str}'.format(**locals())
                if is_html:
                    v = v.replace(',', '<span class=\'hs\'></span>')
                return v
        else:
            return str(value)

    elif isinstance(value, float):
        if value == 0.0:
            return '0'
        if human_readable:
            if unit == '%':
                value *= 100
            precision = 2
            for i in range(10, 1, -1):
                if abs(value) < 1. / (10 ** i):
                    precision = i + 1
                    break
            return '{value:.{precision}f}{unit_str}'.format(**locals())
        else:
            return str(value)

    if isinstance(value, list):
        return ', '.join(format_value(v, unit, human_readable, is_html) for v in value)

    return '.'


def filter_digits(s):
    return ''.join(c for c in s if c.isdigit())


def natural_key(s, _nsre=re.compile('([0-9]+)')):
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split(_nsre, s)]
