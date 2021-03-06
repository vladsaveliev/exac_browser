from utils import *
import itertools

SEARCH_LIMIT = 10000


def get_gene(db, genome, gene_id):
    return db[genome].genes.find_one({'gene_id': gene_id}, projection={'_id': False})


def get_gene_by_name(db, genome, gene_name):
    # try gene_name field first
    gene = db[genome].genes.find_one({'gene_name': gene_name}, projection={'_id': False})
    if gene:
        return gene
    # if not, try gene['other_names']
    return db[genome].genes.find_one({'other_names': gene_name}, projection={'_id': False})


def get_transcript(db, genome, transcript_id):
    transcript = db[genome].transcripts.find_one({'transcript_id': transcript_id}, projection={'_id': False})
    if not transcript:
        return None
    transcript['exons'] = get_exons_in_transcript(db, genome, transcript_id)
    return transcript


def combine_variants(variant, alt_variants, sample_names):
    combined_variant = variant
    combined_variant['sample_names'] = sample_names
    sample_data = [None] * len(sample_names)
    for variant in alt_variants:
        sample_index = combined_variant['sample_names'].index(variant['sample_name'])
        sample_data[sample_index] = variant['sample_data'][0]

    combined_variant['sample_data'] = sample_data
    combined_variant['samples_count'] = sum([1 for x in sample_data if x is not None])
    combined_variant['allele_freq'] = None
    # if combined_variant['rsid'] == '.' or combined_variant['rsid'] is None:
    #    rsid = db[project_genome].dbsnp.find_one({'xpos': xpos})
    #    if rsid:
    #        combined_variant['rsid'] = 'rs%s' % rsid['rsid']
    return combined_variant


def get_variant(db, project_name, project_genome, xpos, ref, alt):
    return db[get_project_key(project_name, project_genome)].combined_variants.find_one({'xpos': xpos, 'ref': ref, 'alt': alt},
                                                                                projection={'_id': False})


def get_variants_by_rsid(db, project_name, genome, rsid):
    if not rsid.startswith('rs'):
        return None
    try:
        int(rsid.lstrip('rs'))
    except Exception, e:
        return None
    variants = list(db[get_project_key(project_name, genome)].variants.find({'rsid': rsid}, projection={'_id': False}))
    add_consequence_to_variants(variants)
    return variants


def get_variants_from_dbsnp(db, project_name, genome, rsid):
    if not rsid.startswith('rs'):
        return None
    try:
        rsid = int(rsid.lstrip('rs'))
    except Exception, e:
        return None
    position = db[get_project_key(project_name, genome)].dbsnp.find_one({'rsid': rsid})
    if position:
        variants = list(db[get_project_key(project_name, genome)].variants.find({'xpos': {'$lte': position['xpos'], '$gte': position['xpos']}}, projection={'_id': False}))
        if variants:
            add_consequence_to_variants(variants)
            return variants
    return []


def get_sample_variants(db, project_name, genome, sample_name, filter_unknown=False):
    key_genes = get_key_genes()
    sample_variants = []
    if filter_unknown:
        variants = db[get_project_key(project_name, genome)].variants.find({'filter': 'PASS', 'sample_name': sample_name})
    else:
        variants = db[get_project_key(project_name, genome)].variants.find({'sample_name': sample_name})
    variants = list(variants)
    for variant in variants:
        if variant['genes'] and (variant['significance'] == 'likely' or variant['significance'] == 'known') and \
                        any(gene in key_genes for gene in variant['genes']):
            sample_variants.append(variant)
    if sample_variants:
        add_consequence_to_variants(sample_variants)
    return sample_variants


def get_filtered_regions_in_project(db, project_name, genome):
    regions = db[get_project_key(project_name, genome)].filtered_regions.find()
    return list(regions)


def get_project_filt_params(db, project_name, genome):
    filt_params = list(db[get_project_key(project_name, genome)].filt_params.find())
    return default_filt_params if not filt_params else filt_params[0]


def get_coverage_for_bases(db, xstart, xstop=None, project_name=None, genome=None, sample_name=None, use_population_data=False):
    """
    Get the coverage for the list of bases given by xstart->xstop, inclusive
    Returns list of coverage dicts
    xstop can be None if just one base, but you'll still get back a list
    """
    if use_population_data:
        coverage_data = db[genome].population_coverage
    elif sample_name:
        coverage_data = db[get_project_key(project_name, genome)][get_sample_key(db, sample_name, project_name, genome)].base_coverage
    else:
        coverage_data = db[get_project_key(project_name, genome)].base_coverage
    if xstop is None:
        xstop = xstart
    coverages = dict(
        (doc['xpos'], doc) for doc in coverage_data.find(
            {'xpos': {'$gte': xstart, '$lte': xstop}},
            projection={'_id': False}
        )
    )
    ret = []
    for i in range(xstart, xstop+1):
        if i in coverages:
            ret.append(coverages[i])
        else:
            ret.append({'xpos': i, 'pos': xpos_to_pos(i)})
    for item in ret:
        item['has_coverage'] = 'mean' in item
        del item['xpos']
    return ret


def get_coverage_for_transcript(db, xstart, xstop=None, project_name=None, genome=None, sample_name=None, use_population_data=False):
    """

    :param db:
    :param genomic_coord_to_exon:
    :param xstart:
    :param xstop:
    :return:
    """
    coverage_array = get_coverage_for_bases(db, xstart, xstop, project_name, genome, sample_name, use_population_data)
    # only return coverages that have coverage (if that makes any sense?)
    # return coverage_array
    covered = [c for c in coverage_array if c['has_coverage']]
    for c in covered:
        del c['has_coverage']
    return covered


def get_constraint_for_transcript(db, genome, transcript):
    return db[genome].constraint.find_one({'transcript': transcript}, projection={'_id': False})


def get_exons_cnvs(db, transcript_name):
   return list(db.cnvs.find({'transcript': transcript_name}, fields={'_id': False}))

def get_cnvs(db, gene_name):
   return list(db.cnvgenes.find({'gene': gene_name}, fields={'_id': False}))


def get_awesomebar_suggestions(autocomplete_strings, query):
    """
    This generates autocomplete suggestions when user
    query is the string that user types
    If it is the prefix for a gene, return list of gene names
    """
    regex = re.compile('^' + re.escape(query), re.IGNORECASE)
    results = (r for r in autocomplete_strings if regex.match(r))
    results = itertools.islice(results, 0, 20)
    return list(results)


# 1:1-1000
R1 = re.compile(r'^(\d+|X|Y|M|MT)\s*:\s*(\d+)-(\d+)$')
R2 = re.compile(r'^(\d+|X|Y|M|MT)\s*:\s*(\d+)$')
R3 = re.compile(r'^(\d+|X|Y|M|MT)$')
# R4 = re.compile(r'^(\d+|X|Y|M|MT)\s*[-:]\s*(\d+)-([ATCG]+)-([ATCG]+)$')
R4 = re.compile(r'^\s*(\d+|X|Y|M|MT)\s*[-:]\s*(\d+)[-:\s]*([ATCG]+)\s*[-:/]\s*([ATCG]+)\s*$')


def get_awesomebar_result(db, project_name, genome, sample_name=None, query=None):
    """
    Similar to the above, but this is after a user types enter
    We need to figure out what they meant - could be gene, variant, region

    Return tuple of (datatype, identifier)
    Where datatype is one of 'gene', 'variant', or 'region'
    And identifier is one of:
    - ensembl ID for gene
    - variant ID string for variant (eg. 1-1000-A-T)
    - region ID string for region (eg. 1-1000-2000)

    Follow these steps:
    - if query is an ensembl ID, return it
    - if a gene symbol, return that gene's ensembl ID
    - if an RSID, return that variant's string


    Finally, note that we don't return the whole object here - only it's identifier.
    This could be important for performance later

    """
    query = query.strip()
    print('Query: %s' % query)

    # Variant
    variant = get_variants_by_rsid(db, project_name, genome, query.lower())
    if variant:
        if len(variant) == 1:
            return 'variant', variant[0]['variant_id']
        else:
            return 'dbsnp_variant_set', variant[0]['rsid']
    variant = get_variants_from_dbsnp(db, project_name, genome, query.lower())
    if variant:
        return 'variant', variant[0]['variant_id']
    # variant = get_variant(db, )
    # TODO - https://github.com/brettpthomas/exac_browser/issues/14

    gene = get_gene_by_name(db, genome, query)
    if gene:
        return 'gene', gene['gene_id']

    # From here out, all should be uppercase (gene, tx, region, variant_id)
    query = query.upper()
    gene = get_gene_by_name(db, genome, query)
    if gene:
        return 'gene', gene['gene_id']

    # Ensembl formatted queries
    if query.startswith('ENS'):
        # Gene
        gene = get_gene(db, genome, query)
        if gene:
            return 'gene', gene['gene_id']

        # Transcript
        transcript = get_transcript(db, genome, query)
        if transcript:
            return 'transcript', transcript['transcript_id']

    # From here on out, only region queries
    if query.startswith('CHR'):
        query = query.lstrip('CHR')
    # Region
    m = R1.match(query)
    if m:
        if int(m.group(3)) < int(m.group(2)):
            return 'region', 'invalid'
        return 'region', '{}-{}-{}'.format(m.group(1), m.group(2), m.group(3))
    m = R2.match(query)
    if m:
        return 'region', '{}-{}-{}'.format(m.group(1), m.group(2), m.group(2))
    m = R3.match(query)
    if m:
        return 'region', '{}'.format(m.group(1))
    m = R4.match(query)
    if m:
        return 'variant', '{}-{}-{}-{}'.format(m.group(1), m.group(2), m.group(3), m.group(4))

    return 'not_found', query


def get_genes_in_region(db, genome, chrom, start, stop):
    """
    Genes that overlap a region
    """
    xstart = get_xpos(chrom, start)
    xstop = get_xpos(chrom, stop)
    genes = db[genome].genes.find({
        'xstart': {'$lte': xstop},
        'xstop': {'$gte': xstart},
    }, projection={'_id': False})
    return list(genes)


def get_variants_in_region(db, project_name, genome, chrom, start, stop, sample_name=None):
    """
    Variants that overlap a region
    Unclear if this will include CNVs
    """
    xstart = get_xpos(chrom, start)
    xstop = get_xpos(chrom, stop)
    variants = []
    search_parameters = {'xpos': {'$lte': xstop, '$gte': xstart}}
    all_variants = find_variants_in_db(db, project_name, genome, search_parameters, sample_name)
    add_consequence_to_variants(all_variants)
    for variant in all_variants:
        remove_extraneous_information(variant)
        variants.append(variant)
    return variants


def get_metrics(db, project_name, genome, variant):
    if 'allele_count' not in variant or variant['allele_num'] == 0:
        return None
    metrics = {}
    for metric in METRICS:
        metrics[metric] = db[get_project_key(project_name, genome)].metrics.find_one({'metric': metric}, projection={'_id': False})

    metric = None
    if variant['allele_count'] == 1:
        metric = 'singleton'
    elif variant['allele_count'] == 2:
        metric = 'doubleton'
    else:
        for af in AF_BUCKETS:
            if float(variant['allele_count'])/variant['allele_num'] < af:
                metric = af
                break
    if metric is not None:
        metrics['Site Quality'] = db[get_project_key(project_name, genome)].metrics.find_one({'metric': 'binned_%s' % metric}, projection={'_id': False})
    return metrics


def remove_extraneous_information(variant):
    # del variant['genotype_depths']
    # del variant['genotype_qualities']
    # del variant['xpos']
    entries_to_remove = ('transcripts', 'genes', 'orig_alt_alleles', 'xstart', 'xstop', 'site_quality', 'vep_annotations')
    for entry in entries_to_remove:
        variant.pop(entry, None)


def check_variant_samples(variant, sample_name):
    sample_index = variant['sample_names'].index(sample_name)
    if not variant['sample_data'][sample_index]:
        return False
    return True


def find_variants_in_db(db, project_name, genome, search_parameters, sample_name=None):
    if sample_name:
        search_parameters['sample_name'] = sample_name
        all_variants = db[get_project_key(project_name, genome)].variants.find(search_parameters, projection={'_id': False},
                                                                               limit=SEARCH_LIMIT)
    else:
        all_variants = db[get_project_key(project_name, genome)].combined_variants.find(search_parameters, projection={'_id': False},
                                                                               limit=SEARCH_LIMIT)
    return all_variants


def get_variants_in_gene(db, project_name, genome, gene_id, sample_name=None):
    """
    """
    search_parameters = {'genes': gene_id}
    all_variants = find_variants_in_db(db, project_name, genome, search_parameters, sample_name)
    variants = []
    for variant in all_variants:
        variant['vep_annotations'] = [x for x in variant['vep_annotations'] if x['Gene_Name'] == gene_id]
        add_consequence_to_variant(variant)
        remove_extraneous_information(variant)
        variants.append(variant)
    return variants


def get_transcripts_in_gene(db, genome, gene_id):
    """
    """
    return list(db[genome].transcripts.find({'gene_id': gene_id}, projection={'_id': False}))


def get_variants_in_transcript(db, project_name, genome, transcript_id, sample_name=None):
    """
    """
    search_parameters = {'transcripts': transcript_id}
    all_variants = find_variants_in_db(db, project_name, genome, search_parameters, sample_name)
    variants = []
    for variant in all_variants:
        variant['vep_annotations'] = [x for x in variant['vep_annotations'] if x['Feature_ID'] == transcript_id]
        add_consequence_to_variant(variant)
        remove_extraneous_information(variant)
        variants.append(variant)
    return variants


def get_exons_in_transcript(db, genome, transcript_id):
    # return sorted(
    #     [x for x in
    #      db.exons.find({'transcript_id': transcript_id}, projection={'_id': False})
    #      if x['feature_type'] != 'exon'],
    #     key=lambda k: k['start'])
    return sorted(list(db[genome].exons.find({'transcript_id': transcript_id,
                                              'feature_type': { "$in": ['CDS', 'UTR', 'Exon', 'exon'] }},
                                              projection={'_id': False})), key=lambda k: k['start'])
