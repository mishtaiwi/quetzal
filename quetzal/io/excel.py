import json
import os
import glob

import numpy as np
import pandas as pd
from tqdm import tqdm


def read_var(file='parameters.xlsx', scenario='base', period=None, return_ancestry=False):
    parameter_frame = pd.read_excel(file, sheet_name='parameters').dropna(axis=1, how='all')
    try:
        types = parameter_frame.set_index(
            ['category', 'parameter']
        )['type'].dropna().to_dict()
    except KeyError:
        types = dict()
    
    if period is not None:
        mask  = ((parameter_frame['period'].isna()) | 
                (parameter_frame['period'].str.casefold() == period.casefold()))
        parameter_frame = parameter_frame[mask]
        parameter_frame.sort_values('period', inplace=True)
        parameter_frame.drop_duplicates(subset=['category','parameter'], inplace=True)
        parameter_frame.sort_index(inplace=True)
    parameter_frame.drop(['description', 'desc', 'unit', 'type', 'period'], axis=1, errors='ignore', inplace=True)
    parameter_frame.set_index(['category', 'parameter'], inplace=True)
    parameter_frame.dropna(how='all', inplace=True)
    if return_ancestry:
        ancestry = get_ancestry(parameter_frame, scenario=scenario)
    for c in parameter_frame.columns:
        parent = parameter_frame[c][('general', 'parent')]
        parameter_frame[c] = parameter_frame[c].fillna(parameter_frame[parent])
    var = parameter_frame[scenario]
    for k, v in types.items():
        try:
            if v == 'float':
                var.loc[k] = float(var.loc[k])
            elif v == 'int':
                var.loc[k] = int(var.loc[k])
            elif v == 'bool':
                var.loc[k] = bool(var.loc[k])
            elif v == 'str':
                var.loc[k] = str(var.loc[k])
            elif v == 'json':
                var.loc[k] = json.loads(var.loc[k])
        except KeyError:
            pass
    if return_ancestry:
        return var, ancestry
    return var


def merge_files(
    parameters_filepath=r'inputs/parameters.xlsx',
    scenario_filepath=r'model/{scenario}/stacks.xlsx',
    merged_filepath=r'outputs/stacks.xlsx'
):
    parameters = pd.read_excel(parameters_filepath)
    scenarios = [c for c in parameters.columns if c not in {'category', 'parameter'}]

    base = scenarios[0]
    base_dict = pd.read_excel(scenario_filepath.format(scenario=base), sheet_name=None)
    pool = {key: [] for key in base_dict.keys()}

    notfound = []
    for scenario in tqdm(scenarios, desc='reading'):
        try:
            df_dict = pd.read_excel(scenario_filepath.format(scenario=scenario), sheet_name=None)
            for key, value in df_dict.items():
                value['scenario'] = scenario
                col = [c for c in value.columns if 'scenario' not in c]
                col.insert(-1, 'scenario')
                value = value[col]
                pool[key].append(value)
        except FileNotFoundError:
            notfound.append(scenario)

    stacks = {k: pd.concat(v) for k, v in pool.items()}
    with pd.ExcelWriter(merged_filepath) as writer:  # doctest: +SKIP
        for name, stack in tqdm(stacks.items(), desc='writing'):
            stack.to_excel(writer, sheet_name=name, index=False)

def get_ancestry(parameter_frame, scenario='base'):
    child = scenario
    ancestry = [child]
    while True:
        parent = parameter_frame.loc[('general','parent'), child]
        if parent == child: break
        ancestry.append(parent)
        child = parent
    return ancestry

def get_filepath(filepath, ancestry=['base'], log=True):
    for scen in ancestry:
        relpath = filepath.format(s=scen)
        if os.path.exists(relpath):
            if log: 
                print(f"specified file found: {relpath}")
            return relpath
        if log:
            print(f"{relpath} does not exist")
    if log:
        print("specified file or input path does not exist")
    return None

def recursive_get_filepaths(path, ancestry=['base'], return_dicts=False, log=True):
    file_filepath = {}
    file_scen = {}

    for scen in ancestry[::-1]:
        filepaths = glob.glob(path.format(s=scen))
        if log: 
            print(f"{len(filepaths)} specified file found in {scen}")
        for filepath in filepaths:
            file = os.path.basename(filepath).split('.')[0]
            if log & (file in file_filepath.keys()):
                print(f"replacing {file} from {file_scen[file]} by {scen}")
            file_filepath[file] = filepath
            file_scen[file] = scen

    if return_dicts:
        return file_filepath, file_scen
    else:
        return list(file_filepath.values())
    
def to_json(file = 'parameters.xlsx', scenario = 'base'):
    var = read_var(file = file, scenario = scenario).drop(('general', 'parent')).to_frame()
    parameter_frame = pd.read_excel(file, sheet_name='parameters').dropna(axis=1, how='all')

    # types
    try:
        types = parameter_frame.set_index(
            ['category', 'parameter']
        )['type'].dropna()
        js_types_dict = {'float': 'Number', 'int': 'Number', 'bool': 'Boolean', 'str': 'String'}
        var = var.join(types)
        var['type'] = var['type'].apply(lambda x: js_types_dict.get(x, x))
    except KeyError:
        var['type'] = np.nan

    # units
    try:
        units = parameter_frame.set_index(
            ['category', 'parameter']
        )['unit'].dropna()
        var = var.join(units)
    except KeyError:
        var['unit'] = np.nan

    # hints
    try:
        hints = parameter_frame.set_index(
            ['category', 'parameter']
        )['description'].dropna()
        var = var.join(hints)
    except KeyError:
        var['description'] = np.nan

    # rules
    try:
        rules = parameter_frame.set_index(
            ['category', 'parameter']
        )['rules'].dropna()
        var = var.join(rules)
    except KeyError:
        var['rules'] = np.full((len(var), 1), ['required']).tolist()

    var = var.reset_index().set_index('category')
    var = var.rename(columns={'parameter': 'text', scenario: 'value', 'unit': 'units', 'description': 'hint'})
    var['name'] = var['text']
    var = var.where(pd.notnull(var), None)

    def records(df):
        if type(df) == pd.core.frame.DataFrame:
            return df.to_dict('records')
        elif type(df) == pd.core.frame.Series:
            return [df.to_dict()]

    js_params = [{'category': cat, 'params': records(var.loc[cat])} for cat in var.index.unique()]

    return js_params