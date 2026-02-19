import pandas as pd
import os
import sys
import urllib.request
import argparse

GROUP_MAPPING = {
    'plant':            'plant',
    'plants':           'plant',
    'weed':             'plant',
    'weeds':            'plant',
    'invertebrate':     'invertebrate',
    'insects':          'invertebrate',
    'insect':           'invertebrate',
    'vertebrate':       'vertebrate_other',
    'mammal':           'vertebrate_mammalian',
    'mammals':          'vertebrate_mammalian',
    'fungi':            'fungi',
    'bacteria':         'bacteria',
    'virus':            'viral',
    'viral':            'viral',
    'protozoa':         'protozoa'
}
def parse_args():
    parser = argparse.ArgumentParser(description="Download Genomes from NCBI (RefSeq & GenBank).")
    
    parser.add_argument('-i', '--input', required=True, help="File with species names (one per line).")
    parser.add_argument('-g', '--group', required=True, help=(
            "Taxonomic group to download.\n"
            "Options: plant, weeds, invertebrate, insects, fungi, bacteria, mammals, etc."
        ))
    parser.add_argument('-o', '--outdir', default=None, help="Directory to save downloaded files. (Default: {group}_genomes)")
    
    # NEW FLAG: Source selection
    parser.add_argument('-s', '--source', default='both', choices=['refseq', 'genbank', 'both'],
                        help="Database to search. 'both' searches both and prioritizes RefSeq. (Default: both)")
    
    parser.add_argument('--clean', action='store_true', help="Force re-download of NCBI assembly summary files.")

    return parser.parse_args()

def download_and_load_summary(ncbi_group, db_source, force_download=False):
    """
    Downloads summary file for a specific DB (refseq or genbank).
    Returns a dataframe tagged with the source.
    """
    # URL pattern: ftp.ncbi.nlm.nih.gov/genomes/refseq/plant/... OR .../genbank/plant/...
    summary_url = f"https://ftp.ncbi.nlm.nih.gov/genomes/{db_source}/{ncbi_group}/assembly_summary.txt"
    local_filename = f"summary_{ncbi_group}_{db_source}.txt"

    if force_download and os.path.exists(local_filename):
        os.remove(local_filename)

    if not os.path.exists(local_filename):
        print(f"[-] Downloading {db_source} summary for '{ncbi_group}'...")
        try:
            urllib.request.urlretrieve(summary_url, local_filename)
        except Exception as e:
            print(f"[!] Error downloading {db_source}: {e}")
            return pd.DataFrame() # Return empty if failed (e.g. group doesn't exist in GenBank)
    
    try:
        df = pd.read_csv(local_filename, sep="\t", header=1, dtype=str, quoting=3) # quoting=3 disables quote processing
        df.columns = df.columns.str.replace('# ', '').str.replace('#', '')
        df['data_source'] = db_source.upper() # Tag the data
        return df
    except Exception as e:
        print(f"[!] Error parsing {local_filename}: {e}")
        return pd.DataFrame()

def rank_assemblies(group_df):
    """
    Ranks assemblies.
    Priority: 
    1. RefSeq Category (Reference > Representative)
    2. Assembly Level (Complete > Chromosome > Scaffold)
    3. Source (RefSeq > GenBank) -- ONLY if category/level are equal
    4. Date (Newest first)
    """
    group_df = group_df.copy()
    
    # Ranking Weights
    cat_map = {'reference genome': 3, 'representative genome': 2, 'na': 1}
    level_map = {'Complete Genome': 4, 'Chromosome': 3, 'Scaffold': 2, 'Contig': 1}
    source_map = {'REFSEQ': 2, 'GENBANK': 1} # Prefer RefSeq if quality is identical
    
    group_df['cat_score'] = group_df['refseq_category'].map(cat_map).fillna(0)
    group_df['lvl_score'] = group_df['assembly_level'].map(level_map).fillna(0)
    group_df['src_score'] = group_df['data_source'].map(source_map).fillna(0)
    
    # Sort
    sorted_df = group_df.sort_values(
        by=['cat_score', 'lvl_score', 'src_score', 'assembly_accession'], 
        ascending=[False, False, False, False]
    )
    return sorted_df.iloc[0]

def main():
    args = parse_args()
    
    # 1. Config
    user_group = args.group.lower()
    ncbi_group = GROUP_MAPPING.get(user_group, user_group)
    
    input_file = args.input
    output_dir = args.outdir if args.outdir else f"{ncbi_group}_genomes"
    report_file = f"download_report_{ncbi_group}.log"
    download_script = f"run_downloads_{ncbi_group}.sh"

    if not os.path.exists(input_file):
        sys.exit(f"[!] Input file '{input_file}' not found.")

    # 2. Load Data (RefSeq, GenBank, or Both)
    dfs = []
    if args.source in ['refseq', 'both']:
        dfs.append(download_and_load_summary(ncbi_group, 'refseq', args.clean))
    if args.source in ['genbank', 'both']:
        dfs.append(download_and_load_summary(ncbi_group, 'genbank', args.clean))
    
    if not dfs or all(d.empty for d in dfs):
        sys.exit("[!] No summary data found. Check internet or group name.")

    # Merge
    full_df = pd.concat(dfs, ignore_index=True)
    print(f"[-] Loaded {len(full_df)} assemblies from {args.source.upper()}.")

    # 3. Process Species
    with open(input_file, 'r') as f:
        target_species = [line.strip() for line in f if line.strip()]

    to_download = []
    
    print(f"[-] Matching {len(target_species)} species...")

    for species in target_species:
        # A. Exact Match
        matches = full_df[full_df['organism_name'] == species]
        
        if not matches.empty:
            best = rank_assemblies(matches)
            to_download.append({
                'name': species,
                'source': best['data_source'],
                'accession': best['assembly_accession'],
                'url': best['ftp_path'],
                'status': 'EXACT_MATCH',
                'level': best['assembly_level']
            })
        else:
            # B. Genus Fallback
            parts = species.split(" ")
            if len(parts) >= 1 and len(parts[0]) > 2:
                genus = parts[0]
                genus_matches = full_df[full_df['organism_name'].str.contains(f"^{genus} ", regex=True, na=False)]
                
                if not genus_matches.empty:
                    best = rank_assemblies(genus_matches)
                    to_download.append({
                        'name': species,
                        'source': best['data_source'],
                        'accession': best['assembly_accession'],
                        'url': best['ftp_path'],
                        'status': f"FALLBACK ({best['organism_name']})",
                        'level': best['assembly_level']
                    })
                else:
                    to_download.append({'name': species, 'source': '-', 'accession': '-', 'url': 'N/A', 'status': 'NOT_FOUND', 'level': '-'})
            else:
                to_download.append({'name': species, 'source': '-', 'accession': '-', 'url': 'N/A', 'status': 'NOT_FOUND', 'level': '-'})

    # 4. Report & Script
    found_count = 0
    with open(report_file, 'w') as f:
        f.write("Target_Species\tStatus\tSource\tAccession\tLevel\tURL\n")
        for item in to_download:
            if item['url'] != "N/A": found_count += 1
            f.write(f"{item['name']}\t{item['status']}\t{item['source']}\t{item['accession']}\t{item['level']}\t{item['url']}\n")

    with open(download_script, 'w') as f:
        f.write("#!/bin/bash\n")
        f.write(f"mkdir -p {output_dir}\n")
        for item in to_download:
            if item['url'] != "N/A":
                # Handle GenBank vs RefSeq FTP path differences (usually they are consistent)
                base_name = os.path.basename(item['url'])
                full_url = f"{item['url']}/{base_name}_genomic.fna.gz"
                f.write(f"echo 'Downloading {item['name']} from {item['source']}...'\n")
                f.write(f"wget -q --show-progress -O {output_dir}/{item['accession']}.fna.gz {full_url}\n")

    print(f"[-] Done. Found {found_count}/{len(target_species)}.")
    print(f"[-] View report: {report_file}")
    print(f"[-] Run download: bash {download_script}")

if __name__ == "__main__":
    main()
