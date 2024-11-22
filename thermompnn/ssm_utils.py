import os
import numpy as np
import re

from omegaconf import OmegaConf
from Bio.PDB import PDBParser

from thermompnn.train_thermompnn import parse_cfg


def get_chains(pdb_file, chain_list):
  # collect list of chains in PDB to match with input
  parser = PDBParser(QUIET=True)
  structure = parser.get_structure('', pdb_file)
  pdb_chains = [c.id for c in structure.get_chains()]

  if chain_list is None: # fill in all chains if left blank
    chain_list = pdb_chains
  elif len(chain_list) < 1:
    chain_list = pdb_chains

  for ch in chain_list:
    assert ch in pdb_chains, f"Chain {ch} not found in PDB file with chains {pdb_chains}"

  return chain_list


def get_config(mode):
    """Grabs relevant configs from disk."""

    current_location = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    local = os.path.join(os.path.join(current_location), 'examples/configs/local.yaml')

    if mode == 'single' or mode == 'additive':
        aux = os.path.join(os.path.join(current_location), 'examples/configs/single.yaml')

    elif mode == 'epistatic':
        aux = os.path.join(os.path.join(current_location), 'examples/configs/epistatic.yaml')
    else:
        raise ValueError("Invalid mode selected!")

    config = OmegaConf.merge(
        OmegaConf.load(local), 
        OmegaConf.load(aux)
    )

    return parse_cfg(config)


def custom_parse_PDB_biounits(x, atoms=['N', 'CA', 'C'], chain=None):
    '''
  input:  x = PDB filename
          atoms = atoms to extract (optional)
  output: (length, atoms, coords=(x,y,z)), sequence
  '''

    alpha_1 = list("ARNDCQEGHILKMFPSTWYV-")
    states = len(alpha_1)
    alpha_3 = ['ALA', 'ARG', 'ASN', 'ASP', 'CYS', 'GLN', 'GLU', 'GLY', 'HIS', 'ILE',
               'LEU', 'LYS', 'MET', 'PHE', 'PRO', 'SER', 'THR', 'TRP', 'TYR', 'VAL', 'GAP']

    aa_1_N = {a: n for n, a in enumerate(alpha_1)}
    aa_3_N = {a: n for n, a in enumerate(alpha_3)}
    aa_N_1 = {n: a for n, a in enumerate(alpha_1)}
    aa_1_3 = {a: b for a, b in zip(alpha_1, alpha_3)}
    aa_3_1 = {b: a for a, b in zip(alpha_1, alpha_3)}

    def AA_to_N(x):
        # ["ARND"] -> [[0,1,2,3]]
        x = np.array(x);
        if x.ndim == 0: x = x[None]
        return [[aa_1_N.get(a, states - 1) for a in y] for y in x]

    def N_to_AA(x):
        # [[0,1,2,3]] -> ["ARND"]
        x = np.array(x);
        if x.ndim == 1: x = x[None]
        return ["".join([aa_N_1.get(a, "-") for a in y]) for y in x]

    xyz, seq, min_resn, max_resn = {}, {}, 1e6, -1e6
    resn_list = []
    for line in open(x, "rb"):
        line = line.decode("utf-8", "ignore").rstrip()

        # handling MSE and SEC residues
        if line[:6] == "HETATM" and line[17:17 + 3] == "MSE":
            line = line.replace("HETATM", "ATOM  ")
            line = line.replace("MSE", "MET")
        elif line[17:17 + 3] == "MSE":
            line = line.replace("MSE", "MET")
        elif line[17:17 + 3] == "SEC":
            line = line.replace("SEC", "CYS")

        if line[:4] == "ATOM":
            ch = line[21:22]
            if ch == chain or chain is None:
                atom = line[12:12 + 4].strip()
                resi = line[17:17 + 3]
                resn = line[22:22 + 5].strip()

                # TODO check for gaps and add them if needed
                if (resn not in resn_list) and len(resn_list) > 0:
                  _, num, ins_code = re.split(r'(\d+)', resn)
                  _, num_prior, ins_code_prior = re.split(r'(\d+)', resn_list[-1])
                  gap = int(num) - int(num_prior) - 1
                  for g in range(gap + 1):
                    resn_list.append(str(int(num_prior) + g))

                # RAW resn is defined HERE
                resn_list.append(resn) # NEED to keep ins code here

                x, y, z = [float(line[i:(i + 8)]) for i in [30, 38, 46]]
                if resn[-1].isalpha():
                    resa, resn = resn[-1], int(resn[:-1]) - 1
                else:
                    resa, resn = "", int(resn) - 1
                if resn < min_resn:
                    min_resn = resn
                if resn > max_resn:
                    max_resn = resn
                if resn not in xyz:
                    xyz[resn] = {}
                if resa not in xyz[resn]:
                    xyz[resn][resa] = {}
                if resn not in seq:
                    seq[resn] = {}
                if resa not in seq[resn]:
                    seq[resn][resa] = resi

                if atom not in xyz[resn][resa]:
                    xyz[resn][resa][atom] = np.array([x, y, z])

    # convert to numpy arrays, fill in missing values
    seq_, xyz_ = [], []
    try:
        for resn in range(min_resn, max_resn + 1):
            if resn in seq:
                for k in sorted(seq[resn]): seq_.append(aa_3_N.get(seq[resn][k], 20))
            else:
                seq_.append(20)

            if resn in xyz:
                for k in sorted(xyz[resn]):
                    for atom in atoms:
                        if atom in xyz[resn][k]:
                            xyz_.append(xyz[resn][k][atom])
                        else:
                            xyz_.append(np.full(3, np.nan))
            else:
                for atom in atoms: xyz_.append(np.full(3, np.nan))
        return np.array(xyz_).reshape(-1, len(atoms), 3), N_to_AA(np.array(seq_)), list(dict.fromkeys(resn_list))
    except TypeError:
        return 'no_chain', 'no_chain', 'no_chain'


def custom_parse_PDB(path_to_pdb, input_chain_list=None, ca_only=False, side_chains=False, mut_chain=None):
    c = 0
    pdb_dict_list = []
    init_alphabet = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T',
                     'U', 'V', 'W', 'X', 'Y', 'Z', 'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n',
                     'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z']
    extra_alphabet = [str(item) for item in list(np.arange(300))]
    chain_alphabet = init_alphabet + extra_alphabet

    if input_chain_list:
        chain_alphabet = input_chain_list

    biounit_names = [path_to_pdb]
    for biounit in biounit_names:
        my_dict = {}
        s = 0
        concat_seq = ''
        concat_N = []
        concat_CA = []
        concat_C = []
        concat_O = []
        concat_mask = []
        coords_dict = {}
        for letter in chain_alphabet:
            if ca_only:
                sidechain_atoms = ['CA']
            elif side_chains:
                sidechain_atoms = ["N", "CA", "C", "O", "CB",
                                   "CG", "CG1", "OG1", "OG2", "CG2", "OG", "SG",
                                   "CD", "SD", "CD1", "ND1", "CD2", "OD1", "OD2", "ND2",
                                   "CE", "CE1", "NE1", "OE1", "NE2", "OE2", "NE", "CE2", "CE3",
                                   "NZ", "CZ", "CZ2", "CZ3", "CH2", "OH", "NH1", "NH2"]
            else:
                sidechain_atoms = ['N', 'CA', 'C', 'O']
            xyz, seq, resn_list = custom_parse_PDB_biounits(biounit, atoms=sidechain_atoms, chain=letter)
            if resn_list != 'no_chain':
              my_dict['resn_list_' + letter] = resn_list
                  # my_dict['resn_list'] = list(resn_list)
            if type(xyz) != str:
                concat_seq += seq[0]
                my_dict['seq_chain_' + letter] = seq[0]
                coords_dict_chain = {}
                if ca_only:
                    coords_dict_chain['CA_chain_' + letter] = xyz.tolist()
                elif side_chains:
                    coords_dict_chain['SG_chain_' + letter] = xyz[:, 11].tolist()
                else:
                    coords_dict_chain['N_chain_' + letter] = xyz[:, 0, :].tolist()
                    coords_dict_chain['CA_chain_' + letter] = xyz[:, 1, :].tolist()
                    coords_dict_chain['C_chain_' + letter] = xyz[:, 2, :].tolist()
                    coords_dict_chain['O_chain_' + letter] = xyz[:, 3, :].tolist()
                my_dict['coords_chain_' + letter] = coords_dict_chain
                s += 1

        fi = biounit.rfind("/")
        # if mut_chain is None:
          # my_dict['resn_list'] = list(resn_list)
        my_dict['name'] = biounit[(fi + 1):-4]
        my_dict['num_of_chains'] = s
        my_dict['seq'] = concat_seq
        # my_dict['resn_list'] = list(resn_list)
        if s <= len(chain_alphabet):
            pdb_dict_list.append(my_dict)
            c += 1
    return pdb_dict_list


def idx_to_pdb_num(pdb, poslist):
  # set up PDB resns and boundaries
  chains = [key[-1] for key in pdb.keys() if key.startswith('resn_list_')]
  resn_lists = [pdb[key] for key in pdb.keys() if key.startswith('resn_list')]
  converter = {}
  offset = 0
  for n, rlist in enumerate(resn_lists):
      chain = chains[n]
      for idx, resid in enumerate(rlist):
          converter[idx + offset] = chain + resid
      offset += idx + 1

  return [converter[pos] for pos in poslist]