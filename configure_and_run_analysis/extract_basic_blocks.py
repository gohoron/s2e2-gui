"""
Copyright (C) Adrian Herrera, 2017

You will need to install r2pipe and pydot:

```
pip install r2pipe pydot
```
"""

from __future__ import print_function

import glob
import json
import os
import sys

import pydot
import r2pipe
import s2e_web.S2E_settings as S2E_settings


def function_addrs(r2):
    """
    Yield a list of all the function's start addresses.
    """
    for addr in r2.cmdj('aflqj'):
        yield int(addr, 16)


def parse_tb_file(path, module):
    """
    Parse a translation block coverage file generated by S2E's
    ``TranslationBlockCoverage`` plugin.
    """
    with open(path, 'r') as f:
        try:
            tb_coverage_data = json.load(f)
        except Exception:
            print('WARN: Failed to parse translation block JSON file %s' % path)
            return None

    if not tb_coverage_data:
        print('WARN: Translation block JSON file %s is empty' % path)
        return None

    if module not in tb_coverage_data:
        print('WARN: Target %s not found in translation block JSON file %s' %
              (module, path))
        return None

    return tb_coverage_data[module]


def basic_block_coverage(r2, translation_blocks):
    """
    Calculate the basic block coverage based on the covered TBs.

    Returns a set of *covered* basic block start addresses
    """
    covered_bbs = set()

    for func_addr in function_addrs(r2):
        graph = r2.cmdj('agj 0x%x' % func_addr)
        assert len(graph) == 1
        graph = graph[0]

        for tb_start_addr, tb_end_addr in translation_blocks:
            for bb in graph['blocks']:
                bb_start_addr = bb['offset']
                bb_end_addr = bb_start_addr + bb['size']

                # Check if the translation block falls within a basic block OR
                # a basic block falls within a translation block
                if (bb_end_addr >= tb_start_addr >= bb_start_addr or
                        bb_start_addr <= tb_end_addr <= bb_end_addr):
                    covered_bbs.add(bb_start_addr)

    return covered_bbs


def render_functions(r2, covered_bbs, output_dir):
    """
    Renders PNG graphs of each of the functions in the program. Basic blocks
    that were executed by S2E are coloured green.

    The resulting PNG images are written to `output_dir`.
    """
    for func_addr in function_addrs(r2):
        dot_str = r2.cmd('ag 0x%x' % func_addr)
        dot = pydot.graph_from_dot_data(dot_str)
        assert len(dot) == 1
        dot = dot[0]

        for node in dot.get_nodes():
            node_name = node.get_name()
            try:
                # XXX This is very hacky - need something more robust
                if node_name.startswith('"'):
                    node_name = node_name[1:-1]
                node_addr = int(node_name, 16)
            except ValueError:
                # Node name is not a hex string
                continue

            if node_addr in covered_bbs:
                node.set_fillcolor('darkolivegreen2')

        with open(os.path.join(output_dir, 'func_0x%x.png' % func_addr), 'wb') as f:
            png = dot.create_png()
            f.write(png)


def generate_graph(s2e_output_dir, s2e_num):
    """
    Generate the PNG graph for the analysis in the output_dir
    """

    s2e_env_path = S2E_settings.S2E_ENVIRONEMENT_FOLDER_PATH
    project_name = S2E_settings.S2E_BINARY_FILE_NAME
    output_dir = os.path.join(s2e_output_dir, "functions")
    os.makedirs(output_dir)

    # Check that the given S2E environment is legitimate
    if not os.path.isfile(os.path.join(s2e_env_path, 's2e.yaml')):
        print('ERROR: %s is not an S2E environment' % s2e_env_path)
        return

    # Check that the given project exists in the environment
    project_path = os.path.join(s2e_env_path, 'projects', project_name)
    if not os.path.isdir(project_path):
        print('ERROR: %s is not a valid project' % project_name)
        return

    # Check that the output directory exists
    if not os.path.isdir(output_dir):
        print('ERROR: %s is not a valid output directory' % output_dir)
        return

    # Check that the project has been executed at least once
    s2e_last_path = os.path.join(project_path, 's2e-last')
    if not os.path.isdir(s2e_last_path):
        print('ERROR: %s has no s2e-last' % project_name)
        return

    # Get all the TB coverage files
    tb_coverage_files = glob.glob(os.path.join(s2e_last_path, '*', 'tbcoverage-*.json')) + \
                        glob.glob(os.path.join(s2e_last_path, 'tbcoverage-*.json'))
    if not tb_coverage_files:
        print('ERROR: No translation block coverage files found in s2e-last. '
              'Did you enable the ``TranslationBlockCoverage`` plugin in '
              's2e-config.lua?')
        return

    # Parse the TB coverage files
    covered_tbs = set()
    for tb_coverage_file in tb_coverage_files:
        # XXX A project can have a different name to the target program
        tb_coverage_data = parse_tb_file(tb_coverage_file, project_name)
        if not tb_coverage_data:
            continue

        covered_tbs.update((start, end) for start, end, _ in tb_coverage_data)

    # Open the program in Radare and do the initial analysis
    # XXX A project can have a different name to the target program
    r2 = r2pipe.open(os.path.join(project_path, project_name))
    r2.cmd('aaa')

    # Calculate the basic block coverage and render the information as a set
    # of PNG images for each function
    covered_bbs = basic_block_coverage(r2, covered_tbs)
    render_functions(r2, covered_bbs, output_dir)
    
    base_path = S2E_settings.S2E_BINARY_FILE_NAME + "/s2e-out-" + str(s2e_num) + "/functions/"
    return [os.path.join(base_path, file) for file in os.listdir(output_dir)]