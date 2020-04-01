"""
	Data processing.
	#TODO: args, format
"""
import argparse
import json

import pandas as pd
import networkx as nx
import numpy as np
import random

from rdkit import Chem
from rdkit.Chem import ChemicalFeatures
from rdkit import RDConfig
from utils.file_utils import *
from utils.functional_utils import get_bond_type_idx, smiles_to_mol


def preprocess_decagon(dir_path='./data/'):
	raw_drugs = {}
	with open(dir_path + 'drug_raw_feat.idx.jsonl') as f:
		for l in f:
			idx, l = l.strip().split('\t')
			raw_drugs[idx] = json.loads(l)

	atom_attr_keys = {a_key for d in raw_drugs.values()
					  for a in d['atoms'] for a_key in a.keys()}
	print('Possible atom attribute names:', atom_attr_keys)
	bond_attr_keys = {b_key for d in raw_drugs.values()
					  for b in d['bonds'] for b_key in b.keys()}
	print('Possible bond attribute names:', bond_attr_keys)

	# # Pre-process steps:
	# ## 1. Calculate the number of Hydrogen for every atom.
	# ## 2. Remove Hydrogens in atom list.
	# ## 3. Get final existing bonds.

	def collate_molecule(molecule, self_loop=True):
		atoms = {a['aid']: a for a in molecule['atoms']}

		bonds = {}
		# adding symmetric bonds: (aid1,aid2) as well as (aid2,aid1)
		for b in molecule['bonds']:
			for aid_pair in [(b['aid1'], b['aid2']),
							 (b['aid2'], b['aid1'])]:
				bonds[aid_pair] = '{}-{}'.format(b['order'], b.get('style', 0))

		if self_loop:
			# Add self loops to the set of existing bonds
			self_bonds = {(aid, aid): 'self' for aid in atoms}
			assert set(self_bonds.keys()) != set(bonds.keys())
			bonds = {**bonds, **self_bonds}

		new_bonds = {}
		# bonds replaces {(b_aid1, b_aid2) : bond_info}
		# with {b_aid1: [(b_aid2, bond_info),...]}
		for aid in atoms:
			atom_vect = []
			for (b_aid1, b_aid2), b in bonds.items():
				if aid == b_aid1:
					atom_vect += [(b_aid2, b)]
			new_bonds[aid] = list(atom_vect)
		bonds = new_bonds

		# Hydrogen bookkeeping
		h_aid_set = {aid for aid, atom in atoms.items() if atom['number'] == 1}

		# {non-hydrogen aid : number of hydrogen bonds it has}
		h_count_dict = {}
		for aid, _ in atoms.items():
			if aid not in h_aid_set:
				hydrogen_neighbour_count = 0
				for nbr, _ in bonds[aid]:
					if nbr in h_aid_set:
						hydrogen_neighbour_count += 1
				h_count_dict[aid] = hydrogen_neighbour_count

		assert len(h_aid_set) == sum(h_count_dict.values())
		assert all([0 == a.get('charge', 0) for a in atoms.values() if a['number'] == 1])

		# Remove Hydrogen and use position as new aid
		atoms_wo_h_new_aid = {}
		# maps from non-hydrogen atoms 'old aid' to
		# a record with features + new aid
		for idx, (aid, a) in enumerate(
			[(aid, a) for aid, a in atoms.items() if a['number'] > 1]):
			atoms_wo_h_new_aid[aid] = {
				**a,
				'charge': a.get('charge', 0),
				'n_hydro': h_count_dict.get(aid, 0),
				'aid': idx
			}

		# Update with new aid
		bonds_wo_h_new_aid = {}
		for aid1, bs in bonds.items():
			if aid1 not in h_aid_set:
				bonds_wo_h_new_aid[atoms_wo_h_new_aid[aid1]['aid']] =\
					[(atoms_wo_h_new_aid[aid2]['aid'], b)
					for aid2, b in bs if aid2 not in h_aid_set]

		atoms_wo_h_new_aid_w_bond = []
		for a in sorted(atoms_wo_h_new_aid.values(), key=lambda x: x['aid']):
			atoms_wo_h_new_aid_w_bond += [
				# adding the neighbour list to the record information
				{**a, 'nbr': bonds_wo_h_new_aid[a['aid']]}]

		assert all(i == a['aid'] for i, a in enumerate(atoms_wo_h_new_aid_w_bond))

		return atoms_wo_h_new_aid_w_bond

	drug_structure_dict = {cid: collate_molecule(d, self_loop=True)
						   for cid, d in raw_drugs.items()}

	#set of bond types (order-style or self) from all molecules in the data set
	bond_types = {b for d in drug_structure_dict.values()
				  for a in d for _, b in a['nbr']}
	# assign a number to each distinct bond type
	bond_type_idx = {b: i for i, b in enumerate(bond_types)}

	print('Bond to idx dict:', bond_type_idx)

	def build_graph_idx_mapping(molecule, bond_type_idx=None):
		atom_type = []
		atom_feat = []

		bond_type = []
		bond_seg_i = []
		bond_idx_j = []

		for i, atom in enumerate(molecule):
			aid = atom['aid']
			assert aid == i

			# atom['nbr'] is of form [(id, bond.order-bond.style)]
			# *atom['nbr'] takes each pair and considers it as a different input
			# zip(*atom['nbr']) build
			# iterator over ids (id1, id2, ...)
			# and iterator over bond info ( bond-order-bond.style1, ...)
			nbr_ids, nbr_bonds = zip(*atom['nbr'])
			assert len(set(nbr_ids)) == len(nbr_ids), 'Multi-graph is not supported.'

			if bond_type_idx:
				nbr_bonds = list(map(bond_type_idx.get, nbr_bonds))

			# Follow position i
			atom_feat += [(atom['number'], atom['n_hydro'], atom['charge'])]
			atom_type += [atom['number']]

			# Follow position i
			bond_type += nbr_bonds

			# Follow aid
			# list with i repeated x times (x is how many bonds i has)
			bond_seg_i += [aid] * len(nbr_ids)
			bond_idx_j += nbr_ids

		return {'n_atom': len(molecule),
				'atom_type': atom_type,
				'atom_feat': atom_feat,
				'bond_type': bond_type,
				'bond_seg_i': bond_seg_i,
				'bond_idx_j': bond_idx_j}

	drug_graph_dict = {
		cid: build_graph_idx_mapping(d, bond_type_idx=bond_type_idx)
		for cid, d in drug_structure_dict.items()}

	# # Write to jsonl file
	with open(dir_path + 'drug.feat.wo_h.self_loop.idx.jsonl', 'w') as f:
		for cid, d in drug_graph_dict.items():
			f.write('{}\t{}\n'.format(cid, json.dumps(d)))
	with open(dir_path + 'drug.bond_idx.wo_h.self_loop.json', 'w') as f:
		f.write(json.dumps(bond_type_idx))


def preprocess_qm9(dir_path='./data/qm9/dsgdb9nsd'):
	# Initialization of graph for QM9
	def extract_graph_properties(prop):
		prop = prop.split()
		g_tag = prop[0]
		g_index = int(prop[1])
		g_A = float(prop[2])
		g_B = float(prop[3])
		g_C = float(prop[4])
		g_mu = float(prop[5])
		g_alpha = float(prop[6])
		g_homo = float(prop[7])
		g_lumo = float(prop[8])
		g_gap = float(prop[9])
		g_r2 = float(prop[10])
		g_zpve = float(prop[11])
		g_U0 = float(prop[12])
		g_U = float(prop[13])
		g_H = float(prop[14])
		g_G = float(prop[15])
		g_Cv = float(prop[16])

		labels = [g_mu, g_alpha, g_homo, g_lumo, g_gap, g_r2, g_zpve, g_U0, g_U, g_H, g_G, g_Cv]

		return {
			"tag": g_tag,
			"index": g_index,
			"A": g_A,
			"B": g_B,
			"C": g_C,
			"mu": g_mu,
			"alpha": g_alpha,
			"homo": g_homo,
			"lumo": g_lumo,
			"gap": g_gap,
			"r2": g_r2,
			"zpve": g_zpve,
			"U0": g_U0,
			"U": g_U,
			"H": g_H,
			"G": g_G,
			"Cv": g_Cv}, labels


	# XYZ file reader for QM9 dataset
	def xyz_graph_reader(graph_file, self_loop = True):
		with open(graph_file, 'r') as f:
			mol_representation = {
				'n_atom': None,
				'atom_type': None,
				'atom_feat': None,
				'bond_type': None,
				'bond_seg_i': None,
				'bond_idx_j': None,
			}

			# Number of atoms
			n_atom = int(f.readline())

			# Graph properties
			properties = f.readline()
			prop_dict, labels = extract_graph_properties(properties)
			mol_idx = prop_dict['index']

			atom_properties = []
			# Atoms properties
			for i in range(n_atom):
				a_properties = f.readline()
				a_properties = a_properties.replace('.*^', 'e')
				a_properties = a_properties.replace('*^', 'e')
				a_properties = a_properties.split()
				atom_properties.append(a_properties)

			# Frequencies
			f.readline()

			# SMILES
			smiles = f.readline()
			smiles = smiles.split()
			smiles = smiles[0]

			mol_representation = smiles_to_mol(smiles=smiles, self_loop=self_loop,
											   coords_flag=True, atom_properties=atom_properties)

			return mol_idx, mol_representation, labels, smiles

	# # Write to jsonl file
	files = [f for f in os.listdir(dir_path) if os.path.isfile(os.path.join(dir_path, f))]
	with open(dir_path + 'viz_drug.feat.self_loop.idx.jsonl', 'w') as f:
		with open(dir_path + 'viz_drug.labels.jsonl', 'w')  as g:
			with open(dir_path + 'viz_smiles.jsonl', 'w') as h:
				for file in files:
					#if ".xyz" in file and file in ["dsgdb9nsd_047721.xyz","dsgdb9nsd_040834.xyz"]: #debugging
					if ".xyz" in file:
						mol_idx, mol_representation, labels, smiles = xyz_graph_reader(os.path.join(dir_path, file))
						f.write('{}\t{}\n'.format(mol_idx, json.dumps(mol_representation)))
						g.write('{}\t{}\n'.format(mol_idx, json.dumps(labels)))
						h.write('{}\t{}\t{}\n'.format(file, mol_idx, json.dumps(smiles)))


def preprocess_drug_comb(dir_path='./drug_comb'):
	drug_data = pd.read_csv(dir_path + "drug_chemical_info.csv")
	dds = pd.read_csv(dir_path + "drugcombs_scored.csv")
	dds.drop(columns=['ID'], inplace=True)
	dds.reset_index(drop=True, inplace=True)

	drug_name_dict = drug_data.set_index('drugName').T.to_dict('dict')

	#for row in dds:
	#	print(row)
	#	print(drug_name_dict[row['Drug1']])
	#	break
	print(dds.columns)
	print(dds['Drug1'])

	join_smiles1 = dds.set_index('Drug1').join(drug_data.set_index('drugName'))
	join_smiles1.index.name = 'Drug1'
	join_smiles1.reset_index(inplace=True)
	join_smiles1.rename(columns={"cIds": "cIds1",
								 "drugNameOfficial": "drugNameOfficial1",
								 "molecularWeight": "molecularWeight1",
								 "smilesString":"smilesString1",
								 "originImgUrl": "originImgUrl1"},
						inplace=True)
	join_smiles1.to_csv(open(dir_path + "jon1.csv", "w"))


	join_smiles2 = join_smiles1.set_index('Drug2').join(drug_data.set_index('drugName'))
	join_smiles2.index.name = 'Drug2'
	join_smiles2.rename(columns={"cIds": "cIds2",
								 "drugNameOfficial": "drugNameOfficial2",
								 "molecularWeight": "molecularWeight2",
								 "smilesString":"smilesString2",
								 "originImgUrl": "originImgUrl2"},
						inplace=True)
	join_smiles2.to_csv(open(dir_path + "smilescombs_scored.csv", "w"))


def main():
	parser = argparse.ArgumentParser(description='Download dataset for Graph Co-attention')
	parser.add_argument('datasets', metavar='D', type=str.lower, nargs='+',
						choices=['qm9', 'decagon', 'drug_comb'],
						help='Name of dataset to download [QM9,DECAGON, DRUG_COMB]')

	# I/O
	parser.add_argument('-p', '--path', metavar='dir', type=str, nargs=1,
						help="path to store the data (default ./data/)")

	args = parser.parse_args()

	# Check parameters
	if args.path is None:
		args.path = './data/'
	else:
		args.path = args.path[0]

	# Init folder
	prepare_data_dir(args.path)

	if 'qm9' in args.datasets:
		preprocess_qm9(args.path + 'qm9/' + 'dsgdb9nsd/')
	elif 'decagon' in args.datasets:
		preprocess_decagon(args.path + 'decagon/')
	elif 'drug_comb' in args.datasets:
		preprocess_drug_comb(args.path + 'drug_comb/')


if __name__ == "__main__":
	main()
