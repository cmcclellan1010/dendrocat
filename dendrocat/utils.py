import numpy as np
from radio_beam import Beams
import astropy.units as u
from astropy.table import MaskedColumn, Column, vstack, Table
from astropy.utils.console import ProgressBar
import matplotlib.pyplot as plt
import matplotlib
from collections import OrderedDict
from copy import deepcopy
import warnings


from .aperture import ellipse, annulus
    
def specindex(nu1, nu2, f1, alpha):
    return f1*(nu2/nu1)**(alpha) 
    
def findrow(idx, catalog):
    idx = int(idx)
    return catalog[np.where(catalog['_idx'] == idx)]

def rms(x):
    return (np.absolute(np.mean(x**2) - (np.mean(x))**2))**0.5

def check_units(quantity, unit=u.deg):
    if isinstance(quantity, Column):
        name = quantity.name
        if quantity.unit is None:
            quantity.unit = unit
            warnings.warn("Assuming quantity is in {}".format(unit))
            return Column(quantity, name=name)
        elif unit.is_equivalent(quantity.unit):
            return Column(quantity.to(unit), name=name)
        else:
            warnings.warn('Non-equivalent unit already exists.')
            return Column(quantity, name=name)
    elif isinstance(quantity, MaskedColumn):
        name = quantity.name
        if quantity.unit is None:
            quantity.unit = unit
            warnings.warn("Assuming quantity is in {}".format(unit))
            return MaskedColumn(quantity, name=name)
        elif unit.is_equivalent(quantity.unit):
            return MaskedColumn(quantity.to(unit), name=name)
        else:
            warnings.warn('Non-equivalent unit already exists.')
            return MaskedColumn(quantity, name=name)
    else:
        if unit.is_equivalent(quantity):
            return quantity.to(unit)
        elif hasattr(quantity, 'unit'):
            warnings.warn('Non-equivalent unit already exists.')
            return quantity
        else:
            return quantity * u.Unit(unit)
            warnings.warn("Assuming quantity is in {}".format(unit))

def commonbeam(major1, minor1, pa1, major2, minor2, pa2):
    """
    Create a smallest bounding ellipse around two other ellipses. 
    Give ellipse dimensions as astropy units quantities.
    """
    major1 = check_units(major1, unit=u.deg)
    minor1 = check_units(minor1, unit=u.deg)
    pa1 = check_units(pa1, unit=u.deg)
    major2 = check_units(major2, unit=u.deg)
    minor2 = check_units(minor2, unit=u.deg)
    pa2 = check_units(pa2, unit=u.deg)
    
    somebeams = Beams([major1.to(u.arcsec), major2.to(u.arcsec)]*u.arcsec, 
                      [minor1.to(u.arcsec), minor2.to(u.arcsec)]*u.arcsec, 
                      [pa1, pa2]*u.deg)
                      
    common = somebeams.common_beam()
    new_major = common._major
    new_minor = common._minor
    new_pa = common._pa
    
    return new_major.to(u.deg), new_minor.to(u.deg), new_pa

def save_regions(catalog, outfile, skip_rejects=True):
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
            fh.write("ellipse({x_cen}, {y_cen}, {major_fwhm}, " \
                     "{minor_fwhm}, {position_angle}) # text={{{_name}}}\n"
                     .format(**dict(zip(row.colnames, row))))



def _matcher(obj1, obj2, verbose=True):
    """
    Find sources that match up between two radio objects. 
    
    Parameters
    ----------
    obj1 : rsprocess.RadioSource object or rsprocess.MasterCatalog object
        A catalog with which to compare radio sources.
    obj2 : rsprocess.RadioSource object or rsprocess.MasterCatalog object
        A catalog with which to compare radio sources.
        
    Returns
    ----------
    astropy.table.Table object
    """
    
    for obj in [obj1, obj2]:
        if not hasattr(obj, 'catalog'):
            obj.to_catalog()
    
    all_colnames = set(obj1.catalog.colnames + obj2.catalog.colnames)        
    stack = vstack([obj1.catalog, obj2.catalog])
    
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
    
    return stack
    

def match_external_cat(cat, shape=None, freq=None, flux_sum=None, flux_peak=None, 
                         err=None, ra=None, dec=None, ):
    '''
    Split a catalog by frequency, to enable better source matching.
    
    Parameters 
    ----------
    cat : astropy.table.Table object
        The catalog to split.
    freq : string or float
        Either the column name of the frequencies in the catalog by which the 
        catalog will be split up, or a float specifying the frequency of every
        entry in the catalog in GHz.
    '''
    
    catalogs = []
    
    if type(freq) is float or type(freq) is int:
        f_GHz = check_units(freq, u.GHz)
        freq_id = '{:.0f}'.format(np.round(f_GHz)).replace(' ', '')
        new_cat = Table(masked=True)
        for col in cat.colnames:
            if flux_sum is not None:
                if flux_sum in col:
                    newsum = MaskedColumn(data=cat[col],
                                              name=freq_id+'_'+shape+'_sum')
                    new_cat.add_column(newsum)
            if flux_peak is not None:
                if flux_peak in col:
                    newpeak = MaskedColumn(data=cat[col],
                                           name=freq_id+'_'+shape+'_peak')
                    new_cat.add_column(newpeak)
            if err is not None:
                if err in col:
                    newerr = MaskedColumn(data=cat[col],
                                          name=freq_id+'_annulus_rms')
                    new_cat.add_column(newerr)
    
        catalogs.append(cat)
        
    elif type(freq) is str:
        for f in set(list(cat[freq_colname])):
            catalog = cat[cat[freq_colname]==f]
            new_cat = Table(masked=True)
            
            f_GHz = check_units(f, u.GHz)
            freq_id = '{:.0f}'.format(np.round(f_GHz)).replace(' ', '')
            
            for col in catalog.colnames:
                if flux_sum is not None:
                    if flux_sum in col:
                        newsum = MaskedColumn(data=catalog[col],
                                              name=freq_id+'_'+shape+'_sum')
                        new_cat.add_column(newsum)
                if flux_peak is not None:
                    if flux_peak in col:
                        newpeak = MaskedColumn(data=catalog[col],
                                               name=freq_id+'_'+shape+'_peak')
                        new_cat.add_column(newpeak)
                if err is not None:
                    if err in col:
                        newerr = MaskedColumn(data=catalog[col],
                                              name=freq_id+'_annulus_rms')
                        new_cat.add_column(newerr)
            catalogs.append(catalog)
    
    else:
        warnings.warn('Frequency not specified. Returning original catalog.')
        catalogs.append(cat)
        
    return catalogs
 
 
def plot_sed(row, catalog, aperture=None, alphas=None, peak=False, log=True, 
             outfile=None):
    '''
    Plot a spectral energy distribution for a specific source in the 
    catalog.
    
    Parameters
    ----------
    idx : str
        The identifier used to specify a row in the MasterCatalog.
    alphas : list of float, optional
        Spectral indices to plot under flux data.
    log : bool, optional
        If enabled, spectral energy distribution will be plotted on a log 
        scale.
    tag_ : string
        tag to search for external photometry data.
    
    
    Examples
    ----------
    '''  
    row = Table(row, masked=True)
    
    if aperture is None:
        aperture = ellipse
    method = ['peak' if peak else 'sum'][0]
    apname = aperture.__name__
    
    # Temporary fix -- only works if all freq_ids are in GHz
    freq_ids = []
    fluxcols = []
    errcols = []
    for i, col in enumerate(catalog.colnames):
        if 'GHz' in col:
            freq_id = col.split('_')[0]
            if row.mask[col][0] == False:
                if apname in col and method in col:
                    freq_ids.append(freq_id)
                    fluxcols.append(col)
                if 'annulus' in col and 'rms' in col:
                    errcols.append(col)
                if 'ellipse' in col and 'err' in col:
                    errcols.append(col)
    
    nus = [float(s.split('GHz')[0]) for s in freq_ids]
    nus, sort = [list(s) for s in zip(*sorted(zip(nus, range(len(nus)))))]
    freq_ids = np.asarray(freq_ids, dtype=object)[sort]
    fluxcols = np.asarray(fluxcols, dtype=object)[sort]
    errcols = np.asarray(errcols, dtype=object)[sort]
    
    fluxes = [row[col][0] for col in fluxcols]
    errs = [row[errcol][0] for errcol in errcols]
    
    x = np.linspace(0.8*np.min(nus), 1.1*np.max(nus), 100)
    ys = []
    
    if alphas:
        if len(fluxes) <= 2:
            for a in alphas:
                constant = fluxes[-1]/(nus[-1]**a)
                ys.append(constant*(x**a))
        else:
            for a in alphas:
                constant = np.median(fluxes)/(np.median(nus)**a)
                ys.append(constant*(x**a))
    
    fig, ax = plt.subplots()
    
    for i in range(len(fluxes)):
        if fluxes[i] < 3.*errs[i]:
            ax.scatter(nus[i], errs[i], marker='v', color='k', zorder=3, label=r'1 $\sigma$')
            ax.scatter(nus[i], 2.*errs[i], marker='v', color='darkred', zorder=3, label=r'2 $\sigma$')
            ax.scatter(nus[i], 3.*errs[i], marker='v', color='red', zorder=3, label=r'3 $\sigma$')
        else:
            ax.errorbar(nus[i], fluxes[i], yerr=errs[i], fmt='o', ms=2, 
                        elinewidth=0.75, color='k', zorder=3,
                        label='Aperture {}'.format(method))
        
    if ys:
        for i, y in enumerate(ys):                     
            ax.plot(x, y, '--',
                    label=r'$\alpha$ = {}'.format(alphas[i], zorder=2))
            
    if log is True:
        ax.set_xscale('log')
        ax.set_yscale('log')
        ax.set_xlabel('Log Frequency (GHz)')
        if peak is True:
            ax.set_ylabel('Log Peak Flux (Jy)')
        else:
            ax.set_ylabel('Log Flux (Jy)')
    else:
        ax.set_xlabel('Frequency (GHz)')
        if peak is True:
            ax.set_ylabel('Peak Flux (Jy)')
        else:
            ax.set_ylabel('Flux (Jy)')
    
    ax.set_title('Spectral Energy Distribution for Source {}'.format(row['_name'][0]))
    ax.xaxis.set_major_locator(matplotlib.ticker.FixedLocator([int(nu) for nu in nus]))                
    ax.xaxis.set_major_formatter(matplotlib.ticker.FixedFormatter(['{:.1f} GHz'.format(nu) for nu in nus]))
    ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    plt.xticks(rotation=-90)
    handles, labels = plt.gca().get_legend_handles_labels()
    label = OrderedDict(zip(labels, handles))
    ax.legend(label.values(), label.keys())
    plt.tight_layout()
    
    if outfile is not None:
        ax.savefig(outfile, dpi=300, bbox_inches='tight')
    
    return nus, fluxes, errs

