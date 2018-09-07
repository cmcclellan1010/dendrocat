import numpy as np
from radio_beam import Beams
import astropy.units as u
from astropy.table import MaskedColumn, Column, vstack, Table
from astropy.coordinates import SkyCoord
from astropy.utils.console import ProgressBar
import matplotlib.pyplot as plt
from collections import OrderedDict
from copy import deepcopy
import warnings
import regions
    
    
class NonEquivalentError(Exception):
    pass
    
def get_index_masked(table):
    """
    Returns indices of rows in a table that contain one or more masked entries.

    Parameters
    ----------
    table : ~astropy.table.Table
        The table to check for masked entries.

    Returns
    -------
    ~numpy.ndarray
    """

    ind = []
    try:
        for i, row in enumerate(table.mask):
            if np.array(list(row)).any():
                ind.append(i)
    except TypeError: 
        for i, somebool in enumerate(table.mask):
            if somebool:
                ind.append(i)
    
    return np.array(ind)


def specindex(nu1, nu2, f1, alpha):
    return f1*(nu2/nu1)**(alpha) 
    
def findrow(idx, catalog):
    idx = int(idx)
    return catalog[np.where(catalog['_idx'] == idx)]

def rms(x):
    return (np.absolute(np.mean(x**2) - (np.mean(x))**2))**0.5
    
def load(infile):
    filename = infile.split('.')[0]+'.pickle'
    with open(filename, 'rb') as f:
        return pickle.load(f)

def ucheck(quantity, unit):

    if isinstance(quantity, Column):
        name = quantity.name
        if quantity.unit is None:
            quantity.unit = unit
            warnings.warn("Assuming quantity is in {}".format(unit))
            return Column(quantity, name=name)
        elif unit.is_equivalent(quantity.unit):
            return Column(quantity.to(unit), name=name)
        else:
            raise NonEquivalentError("Non-equivalent units")
            
    elif isinstance(quantity, MaskedColumn):
        name = quantity.name
        if quantity.unit is None:
            quantity.unit = unit
            warnings.warn("Assuming quantity is in {}".format(unit))
            return MaskedColumn(quantity, name=name)
        elif unit.is_equivalent(quantity.unit):
            return MaskedColumn(quantity.to(unit), name=name)
        else:
            raise NonEquivalentError("Non-equivalent units")
            
    elif isinstance(quantity, regions.PixCoord):
        if unit.is_equivalent(u.pix):
            return quantity
        else:
            raise NonEquivalentError("Non-equivalent units")
    
    elif isinstance(quantity, SkyCoord):
        if unit.is_equivalent(u.deg):
            return quantity
        else:
            raise NonEquivalentError("Non-equivalent units")
            
    elif type(quantity) == list or type(quantity) == tuple:
        existing_units = []
        for item in quantity:
            try:
                existing_units.append(item.unit)
            except AttributeError:
                existing_units.append(None)
        
        if all(u1 is None for u1 in existing_units):
            warnings.warn("Assuming quantity is in {}".format(unit))
            return quantity*unit
        else:
            for u1 in existing_units:
                all_except_u1 = [x for x in existing_units if x != u1]
                for u2 in all_except_u1:
                    if u1 is not None and u2 is not None:
                        if u1.is_equivalent(u2):
                            pass
                        else:
                            raise NonEquivalentError("Non-equivalent units")
                    elif u1 is not None and u2 is None:
                        raise NonEquivalentError("Cannot mix units and scalars")
                    elif u1 is None and u2 is not None:
                         raise NonEquivalentError("Cannot mix units and scalars")
        return [item.to(unit) for item in quantity]*unit
            
    else: # Unit is a single scalar or unit
        if unit.is_equivalent(quantity):
            return quantity.to(unit)
        elif hasattr(quantity, 'unit'):
            raise NonEquivalentError("Non-equivalent units")
        else:
            return quantity * unit
            warnings.warn("Assuming quantity is in {}".format(unit))

def commonbeam(major1, minor1, pa1, major2, minor2, pa2):
    """
    Create a smallest bounding ellipse around two other ellipses. 
    Give ellipse dimensions as astropy units quantities.
    """
    major1 = ucheck(major1, unit=u.deg)
    minor1 = ucheck(minor1, unit=u.deg)
    pa1 = ucheck(pa1, unit=u.deg)
    major2 = ucheck(major2, unit=u.deg)
    minor2 = ucheck(minor2, unit=u.deg)
    pa2 = ucheck(pa2, unit=u.deg)
    
    somebeams = Beams([major1.to(u.arcsec), major2.to(u.arcsec)]*u.arcsec, 
                      [minor1.to(u.arcsec), minor2.to(u.arcsec)]*u.arcsec, 
                      [pa1, pa2]*u.deg)
                      
    common = somebeams.common_beam()
    new_major = common._major
    new_minor = common._minor
    new_pa = common._pa
    
    return new_major.to(u.deg), new_minor.to(u.deg), new_pa

def saveregions(catalog, outfile, skip_rejects=True):
    """
    Save a catalog as a a DS9 region file.
    
    Parameters
    ----------
    catalog : astropy.table.Table, RadioSource, or MasterCatalog object
        The catalog or catalog-containing object from which to extract source
        coordinates and ellipse properties.
    outfile : str
        Path to save the region file.
    skip_rejects : bool, optional
        If enabled, rejected sources will not be saved. Default is True
    """
    
    if outfile.split('.')[-1] != 'reg':
        warnings.warn('Invalid or missing file extension. Self-correcting.')
        outfile = outfile.split('.')[0]+'.reg'
    
    if skip_rejects:
        catalog = catalog[np.where(catalog['rejected'] == 0)]
            
    with open(outfile, 'w') as fh:
        fh.write("icrs\n")
        for row in catalog:
            fh.write("ellipse({}, {}, {}, {}, {}) # text={{}}\n"
                     .format(row['x_cen'], row['y_cen'], row['major_fwhm']/2.,
                             row['minor_fwhm']/2., row['position_angle'], 
                             row['_name']))


def match(*args, verbose=True):
    
    """
    Find sources that match up between any number of dendrocat objects. 
    
    Parameters
    ----------
    *args : `~dendrocat.Radiosource`, `~dendrocat.Mastercatalog`, or `~astropy.table.Table` object
        A catalog with which to compare radio sources.
    verbose : bool, optional
        If enabled, output is fed to the console.
        
    Returns
    ----------
    `~dendrocat.MasterCatalog` object
    """
    
    from .mastercatalog import MasterCatalog
    
    current_arg = args[0]
    for k in range(len(args)-1):
        arg1 = current_arg
        arg2 = args[k+1]

        all_colnames = set(arg1.catalog.colnames + arg2.catalog.colnames)        
        stack = vstack([arg1.catalog, arg2.catalog])
        
        all_colnames.add('_index')
        try:
            stack.add_column(Column(range(len(stack)), name='_index'))
        except ValueError:
            stack['_index'] = range(len(stack))
        stack = stack[sorted(list(all_colnames))]
        
        rejected = np.where(stack['rejected'] == 1)[0]
        
        if verbose:
            print('Combining matches')
            pb = ProgressBar(len(stack) - len(rejected))
        
        i = 0
        while True:
            
            if i == len(stack) - 1:
                break
            
            if i in rejected:
                i += 1
                continue
            
            teststar = stack[i]
            delta_p = deepcopy(stack[stack['rejected']==0]['_idx', '_index', 'x_cen', 'y_cen'])
            delta_p.remove_rows(np.where(delta_p['_index']==teststar['_index'])[0])
            delta_p['x_cen'] = np.abs(delta_p['x_cen'] - teststar['x_cen'])                
            delta_p['y_cen'] = np.abs(delta_p['y_cen'] - teststar['y_cen'])
            delta_p.sort('x_cen')
            
            threshold = 1e-5
            found_match = False
            
            dist_col = MaskedColumn(length=len(delta_p), name='dist', 
                                    mask=True)
            
            for j in range(10):
                dist_col[j] = np.sqrt(delta_p[j]['x_cen']**2. 
                                      + delta_p[j]['y_cen']**2)            
                if dist_col[j] <= threshold:
                    found_match = True
                    
            delta_p.add_column(dist_col)
            delta_p.sort('dist')
            
            if found_match:
                match_index = np.where(stack['_index'] == delta_p[0]['_index'])
                match = deepcopy(stack[match_index])
                stack.remove_row(match_index[0][0])
                
                # Find the common bounding ellipse
                new_x_cen = np.average([match['x_cen'], teststar['x_cen']])
                new_y_cen = np.average([match['y_cen'], teststar['y_cen']])
                
                # Find new ellipse properties
                new_maj, new_min, new_pa = commonbeam(
                                             float(match['major_fwhm']), 
                                             float(match['minor_fwhm']), 
                                             float(match['position_angle']),
                                             float(teststar['major_fwhm']),
                                             float(teststar['minor_fwhm']),
                                             float(teststar['position_angle'])
                                             )
                
                # Replace properties of test star
                stack[i]['x_cen'] = new_x_cen       
                stack[i]['y_cen'] = new_y_cen
                stack[i]['major_fwhm'] = new_maj.value
                stack[i]['minor_fwhm'] = new_min.value
                stack[i]['position_angle'] = new_pa.value
        
                # Replace masked data with available values from the match
                for k, masked in enumerate(stack.mask[i]):
                    colname = stack.colnames[k]
                    if masked:
                        stack[i][colname] = match[colname]
            i += 1
            if verbose:
                pb.update()
        
        # Fill masked detection column fields with 'False'
        for colname in stack.colnames:
            if 'detected' in colname:
                stack[colname].fill_value = 0

        stack['_index'] = range(len(stack))
        current_arg = MasterCatalog(arg1, arg2, catalog=stack)
    return current_arg

