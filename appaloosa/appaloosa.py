"""
script to carry out flare finding in Kepler LC's

"""

import numpy as np
import os.path
from os.path import expanduser
import time
import datetime
from version import __version__
from aflare import aflare1
import detrend
from gatspy.periodic import LombScargleFast
import warnings
import matplotlib.pyplot as plt
import pandas as pd
from scipy import stats
from scipy.optimize import curve_fit
from scipy.signal import wiener
from scipy import signal
from astropy.io import fits
import matplotlib
import glob
matplotlib.rcParams.update({'font.size':18})
matplotlib.rcParams.update({'font.family':'serif'})
from scipy.signal import savgol_filter

# from rayleigh import RayleighPowerSpectrum
try:
    import MySQLdb
    haz_mysql = True
except ImportError:
    haz_mysql = False


def chisq(data, error, model):
    '''
    Compute the normalized chi square statistic:
    chisq =  1 / N * SUM(i) ( (data(i) - model(i))/error(i) )^2
    '''
    return np.sum( ((data - model) / error)**2.0 ) / np.size(data)


def GetLCdb(objectid, type='', readfile=False,
          savefile=False, exten = '.lc.gz',
          onecadence=False):
    '''
    Retrieve the lightcurve/data from the UW database.

    Parameters
    ----------
    objectid
    type : str, optional
        If either 'slc' or 'llc' then just get 1 type of cadence. Default
        is empty, so gets both
    readfile : bool, optional
        Default is False
    savefie : bool, optional
        Default is False
    exten : str, optional
        Extension for file saving. Default is '.lc.gz'
    onecadence : bool, optional
        For quarters with Long and Short cadence, remove the Long data.
        Default is False. Can be done later to the data output

    Returns
    -------
    numpy array with many columns:
        QUARTER, TIME, PDCSAP_FLUX, PDCSAP_FLUX_ERR,
        SAP_QUALITY, LCFLAG, SAP_FLUX, SAP_FLUX_ERR
    '''

    isok = 0 # a flag to check if database returned sensible answer
    ntry = 0

    if readfile is True:
        # attempt to find file in working dir
        if os.path.isfile(str(objectid) + exten):
            data = np.loadtxt(str(objectid) + exten)
            isok = 101


    while isok<1:
        # this holds the keys to the db... don't put on github!
        auth = np.loadtxt('auth.txt', dtype='string')

        # connect to the db
        db = MySQLdb.connect(passwd=auth[2], db="Kepler",
                             user=auth[1], host=auth[0])

        query = 'SELECT QUARTER, TIME, PDCSAP_FLUX, PDCSAP_FLUX_ERR, ' +\
                'SAP_QUALITY, LCFLAG, SAP_FLUX, SAP_FLUX_ERR ' +\
                'FROM Kepler.source WHERE KEPLERID=' + str(objectid)

        # only get SLC or LLC data if requested
        if type=='slc':
            query = query + ' AND LCFLAG=0 '
        if type=='llc':
            query = query + ' AND LCFLAG=1 '

        query = query + ' ORDER BY TIME;'

        # make a cursor to the db
        cur = db.cursor()
        cur.execute(query)

        # get all the data
        rows = cur.fetchall()

        # convert to numpy data array
        data = np.asarray(rows)

        # close the cursor to the db
        cur.close()

        # make sure the database returned the actual light curve
        if len(data[:,0] > 99):
            isok = 10
        # only try 10 times... shouldn't ever need this limit
        if ntry > 9:
            isok = 2
        ntry = ntry + 1
        time.sleep(10) # give the database a breather

    if onecadence is True:
        data_raw = data.copy()
        data = OneCadence(data_raw)

    if savefile is True:
        # output a file in working directory
        np.savetxt(str(objectid) + exten, data)

    return data



def Get(mode, file, objectid, win_size=3):
    
    '''
    
    Call a get function depending on the mode, 
    processes loading steps common to all modes.
    Generates error from short time median scatter
    if not given elsewhere.
    
    Parameters: 
    ------------
    file: lightcurve file location
    mode: type of light curve, e.g. EVEREST LC, 
          Vanderburg LC, raw MAST .fits file etc.
    win_size: window size for scatter generator
    
    Returns:
    ------------
    lc: light curve DataFrame
    
    '''
    
    def GetObjectID(mode):
        if mode == 'fits':
            return str(int( file[file.find('kplr')+4:file.find('-')]))
        elif mode == 'ktwo':
            return str(int( file[file.find('ktwo')+4:file.find('-')] ))
        elif mode == 'vdb':
            str(int(file[file.find('lightcurve_')+11:file.find('-')]))
        elif mode == 'everest':
            return str(int(file[file.find('everest')+15:file.find('-')]))
        elif mode == 'txt':
            return file[0:3]
        elif mode == 'csv':
            return '0000'
        
    def GetOutfile(mode,file):
        
        if mode == 'everest':
            fldr = objectid[0:3]
            home = expanduser("~")
            outdir = home + '/research/k2_cluster_flares/aprun/' + fldr + '/'
            if not os.path.isdir(outdir):
                try:
                    os.makedirs(outdir)
                except OSError:
                    pass
            return outdir + file[file.find('everest')+15:]
        
        elif mode == 'fits':
            fldr = objectid[0:3]
            outdir = 'aprun/' + fldr + '/'
            if not os.path.isdir(outdir):
                try:
                    os.makedirs(outdir)
                except OSError:
                    pass
            return outdir + file[file.find('kplr'):]
        
        elif mode in ('vdb','csv'):
            fldr = objectid[0:3]
            home = expanduser("~")
            outdir = home + '/research/k2_cluster_flares/aprun/' + fldr + '/'
            if not os.path.isdir(outdir):
                try:
                    os.makedirs(outdir)
                except OSError:
                    pass
            return outdir + file[file.find('lightcurve_')+11:]
        
        elif mode in ('txt','ktwo'):
            return file

    modes = {'fits': GetLCfits,
             'ktwo': GetLCfits, 
             'vdb': GetLCvdb, 
             'everest': GetLCeverest, 
             'txt': GetLCtxt, 
             'csv': GetLCvdb}
    
    lc = modes[mode](file).dropna(how='any')

    t = lc.time.values
    dt = np.nanmedian(t[1:] - t[0:-1])
    if (dt < 0.01):
        dtime = 54.2 / 60. / 60. / 24.
    else:
        dtime = 30 * 54.2 / 60. / 60. / 24.
    
    lc['exptime'] = dtime
    lc['qtr'] = 0
    
    if 'quality' not in lc.columns:
        lc['quality'] = 0
        
    if 'error' not in lc.columns:
        lc['error'] = np.nanmedian(lc.flux_raw.rolling(win_size, center=True).std())

    return GetOutfile(mode, file), GetObjectID(mode), np.array(lc.qtr), np.array(lc.time), np.array(lc.quality), np.array(lc.exptime), np.array(lc.flux_raw), np.array(lc.error)
        
def GetLCfits(file):
    
    '''
    Parameters
    ----------
    file : light curve file location for a MAST archive .fits file

    Returns
    -------
    lc: light curve DataFrame with columns [time, quality, flux_raw, error]
    '''

    hdu = fits.open(file)
    data_rec = hdu[1].data
    lc = pd.DataFrame({'time':data_rec['TIME'].byteswap().newbyteorder(),
                      'flux_raw':data_rec['SAP_FLUX'].byteswap().newbyteorder(),
                      'error':data_rec['SAP_FLUX_ERR'].byteswap().newbyteorder(),
                      'quality':data_rec['SAP_QUALITY'].byteswap().newbyteorder()})
    

    return lc


def GetLCvdb(file):

    '''
    Parameters
    ----------
    file : light curve file location for a Vanderburg de-trended .txt file

    Returns
    -------
    lc: light curve DataFrame with columns [time, flux_raw]
    '''
    
    lc = pd.read_csv(file,index_col=False)
    lc.rename(index=str, 
              columns={'BJD - 2454833': 'time',' Corrected Flux':'flux_raw'},
              inplace=True,
              )
    return lc


def GetLCeverest(file):
    
    '''
    Parameters
    ----------
    file : light curve file location for a Vanderburg de-trended .txt file

    Returns
    -------
    lc: light curve DataFrame with columns [time, flux_raw]
    '''
    
    hdu = fits.open(file)
    data_rec = hdu[1].data
    lc = pd.DataFrame({'time':np.array(data_rec['TIME']).byteswap().newbyteorder(),
                      'flux_raw':np.array(data_rec['FLUX']).byteswap().newbyteorder(),})
    #keep the outliers... for now
    #lc['quality'] = data_rec['OUTLIER'].byteswap().newbyteorder()
  
    return lc


def GetLCtxt(file):

    '''
    Parameters
    ----------
    file : light curve file location for a basic .txt file

    Returns
    -------
    lc: light curve DataFrame with columns [time, flux_raw, error]
    '''
    
    lc = pd.read_csv(file,
                     index_col=False,
                     usecols=(0,1,2),
                     skiprows=1,
                     header = None,
                     names = ['time','flux_raw','error'])
        
    return lc


def OneCadence(data):
    '''
    Within each quarter of data from the database, pick the data with the
    fastest cadence. We want to study 1-min if available. Don't want
    multiple cadence observations in the same quarter, bad for detrending.

    Parameters
    ----------
    data : numpy array
        the result from MySQL database query, using the getLC() function

    Returns
    -------
    Data array

    '''
    # get the unique quarters
    qtr = data[:,0]
    cadence = data[:,5]
    uQtr = np.unique(qtr)

    indx = []

    # for each quarter, is there more than one cadence?
    for q in uQtr:
        x = np.where( (np.abs(qtr-q) < 0.1) )

        etimes = np.unique(cadence[x])
        y = np.where( (cadence[x] == min(etimes)) )

        indx = np.append(indx, x[0][y])

    indx = np.array(indx, dtype='int')

    data_out = data[indx,:]
    return data_out

def func_specific(wert):
    t = 3
    return func(t,wert)

def DetectCandidate(time, flux, error, flags, model,
                    error_cut=2, gapwindow=0.1, minsep=3,
                    returnall=False):
    '''
    Detect flare candidates using sigma threshold, toss out bad flags.
    Uses very simple sigma cut to find significant points.

    Parameters
    ----------
    time :
    flux :
    error :
    flags :
    model :
    error_cut : int, optional
        the sigma threshold to select outliers (default is 2)
    gapwindow : float, optional
        The duration of time around data gaps to ignore flare candidates
        (default is 0.1 days)
    minsep : int, optional
        The number of datapoints required between individual flare events
        (default is 3)

    Returns
    -------
    (flare start index, flare stop index)

    if returnall=True, returns
    (flare start index, flare stop index, candidate indicies)

    '''

    bad = FlagCuts(flags, returngood=False)

    chi = (flux - model) / error

    # find points above sigma threshold, and passing flag cuts
    cand1 = np.where((chi >= error_cut) & (bad < 1))[0]

    _, dl, dr = detrend.FindGaps(time) # find edges of time windows
    for i in range(0, len(dl)):
        x1 = np.where((np.abs(time[cand1]-time[dr[i]-1]) < gapwindow))
        x2 = np.where((np.abs(time[cand1]-time[dl[i]]) < gapwindow))
        cand1 = np.delete(cand1, x1)
        cand1 = np.delete(cand1, x2)

    # find start and stop index, combine neighboring candidates in to same events
    cstart = cand1[np.append([0], np.where((cand1[1:]-cand1[:-1] > minsep))[0]+1)]
    cstop = cand1[np.append(np.where((cand1[1:]-cand1[:-1] > minsep))[0], [len(cand1)-1])]

    # for now just return index of candidates
    if returnall is True:
        return cstart, cstop, cand1
    else:
        return cstart, cstop


def FINDflare(flux, error, N1=3, N2=1, N3=3,
              avg_std=False, std_window=7,
              returnbinary=False, debug=False):
    '''
    The algorithm for local changes due to flares defined by
    S. W. Chang et al. (2015), Eqn. 3a-d
    http://arxiv.org/abs/1510.01005

    Note: these equations originally in magnitude units, i.e. smaller
    values are increases in brightness. The signs have been changed, but
    coefficients have not been adjusted to change from log(flux) to flux.

    Note: this algorithm originally ran over sections without "changes" as
    defined by Change Point Analysis. May have serious problems for data
    with dramatic starspot activity. If possible, remove starspot first!

    Parameters
    ----------
    flux : numpy array
        data to search over
    error : numpy array
        errors corresponding to data.
    N1 : int, optional
        Coefficient from original paper (Default is 3)
        How many times above the stddev is required.
    N2 : int, optional
        Coefficient from original paper (Default is 1)
        How many times above the stddev and uncertainty is required
    N3 : int, optional
        Coefficient from original paper (Default is 3)
        The number of consecutive points required to flag as a flare
    avg_std : bool, optional
        Should the "sigma" in this data be computed by the median of
        the rolling().std()? (Default is False)
        (Not part of original algorithm)
    std_window : float, optional
        If avg_std=True, how big of a window should it use?
        (Default is 25 data points)
        (Not part of original algorithm)
    returnbinary : bool, optional
        Should code return the start and stop indicies of flares (default,
        set to False) or a binary array where 1=flares (set to True)
        (Not part of original algorithm)
    '''

    med_i = np.nanmedian(flux)

    if debug is True:
        print("DEBUG: med_i = " + str(med_i))

    if avg_std is False:
        sig_i = np.nanstd(flux) # just the stddev of the window
    else:
        # take the average of the rolling stddev in the window.
        # better for windows w/ significant starspots being removed
        sig_i = np.nanmedian(pd.Series(flux).rolling(std_window, center=True).std())
    if debug is True:
        print("DEBUG: sig_i = " + str(sig_i))

    ca = flux - med_i
    cb = np.abs(flux - med_i) / sig_i
    cc = np.abs(flux - med_i - error) / sig_i

    if debug is True:
        print("DEBUG: ")
        print(sum(ca>0))
        print(sum(cb>N1))
        print(sum(cc>N2))

    # pass cuts from Eqns 3a,b,c
    ctmp = np.where((ca > 0) & (cb > N1) & (cc > N2))

    cindx = np.zeros_like(flux)
    cindx[ctmp] = 1

    # Need to find cumulative number of points that pass "ctmp"
    # Count in reverse!
    ConM = np.zeros_like(flux)
    # this requires a full pass thru the data -> bottleneck
    for k in range(2, len(flux)):
        ConM[-k] = cindx[-k] * (ConM[-(k-1)] + cindx[-k])

    # these only defined between dl[i] and dr[i]
    # find flare start where values in ConM switch from 0 to >=N3
    istart_i = np.where((ConM[1:] >= N3) &
                        (ConM[0:-1] - ConM[1:] < 0))[0] + 1

    # use the value of ConM to determine how many points away stop is
    istop_i = istart_i + (ConM[istart_i] - 1)

    istart_i = np.array(istart_i, dtype='int')
    istop_i = np.array(istop_i, dtype='int')

    if returnbinary is False:
        return istart_i, istop_i
    else:
        bin_out = np.zeros_like(flux, dtype='int')
        for k in range(len(istart_i)):
            bin_out[istart_i[k]:istop_i[k]+1] = 1
        return bin_out


def FlagCuts(flags, bad_flags = (16, 128, 2048), returngood=True):

    '''
    return the indexes that pass flag cuts

    Ethan says cut out [16, 128, 2048], can add more later.
    '''

    # convert flag array to type int, just in case
    flags_int = np.array(flags, dtype='int')
    # empty array to hold places where bad flags exist
    bad = np.zeros_like(flags)

    # these are the specific flags to reject on
    # NOTE: == 2**[4, 7, 11]
    # bad_flgs = [16, 128, 2048]

    # step thru each of the bitwise flags, find where exist
    for k in bad_flags:
        bad = bad + np.bitwise_and(flags_int, k)

    # find places in array where NO bad flags are set
    if returngood is True:
        good = np.where((bad < 1))[0]
        return good
    else:
        return bad


def EquivDur(time, flux):
    '''
    Compute the Equivalent Duration of an event. This is simply the area
    under the flare, in relative flux units.

    Flux must be array in units of zero-centered RELATIVE FLUX

    Time must be array in units of DAYS

    Output has units of SECONDS
    '''

    p = np.trapz(flux, x=(time * 60.0 * 60.0 * 24.0))
    return p


def FlareStats(time, flux, error, model, istart=-1, istop=-1,
               c1=(-1,-1), c2=(-1,-1), cpoly=2, ReturnHeader=False):
    '''
    Compute properties of a flare event. Assumes flux is in relative flux units,
    i.e. rel_flux = (flux - median) / median

    Parameters
    ----------
    time : 1d numpy array
    flux : 1d numpy array
    error : 1d numpy array
    model : 1d numpy array
        These 4 arrays must have the same number of elements
    istart : int, optional
        The index in the input arrays (time,flux,error,model) that the
        flare starts at. If not used, defaults to the first data point.
    istop : int, optional
        The index in the input arrays (time,flux,error,model) that the
        flare ends at. If not used, defaults to the last data point.

    '''

    # if FLARE indicies are not stated by user, use start/stop of data
    if (istart < 0):
        istart = 0
    if (istop < 0):
        istop = len(flux)-1

    # can't have flare start/stop at same point
    if (istart == istop):
        istop = istop + 1
        istart = istart - 1

    # need to have flare at least 3 datapoints long
    if (istop-istart < 2):
        istop = istop + 1

    # print(istart, istop) # % ;

    tstart = time[istart]
    tstop = time[istop]
    dur0 = tstop - tstart

    # define continuum regions around the flare, same duration as
    # the flare, but spaced by half a duration on either side
    if (c1[0]==-1):
        t0 = tstart - dur0
        t1 = tstart - dur0/2.
        c1 = np.where((time >= t0) & (time <= t1))
    if (c2[0]==-1):
        t0 = tstop + dur0/2.
        t1 = tstop + dur0
        c2 = np.where((time >= t0) & (time <= t1))

    flareflux = flux[istart:istop+1]
    flaretime = time[istart:istop+1]
    modelflux = model[istart:istop+1]
    flareerror = error[istart:istop+1]

    contindx = np.concatenate((c1[0], c2[0]))
    if (len(contindx) == 0):
        # if NO continuum regions are found, then just use 1st/last point of flare
        contindx = np.array([istart, istop])
        cpoly = 1
    contflux = flux[contindx] # flux IN cont. regions
    conttime = time[contindx]
    contfit = np.polyfit(conttime, contflux, cpoly)
    contline = np.polyval(contfit, flaretime) # poly fit to cont. regions

    medflux = np.nanmedian(model)

    # measure flare amplitude
    ampl = np.max(flareflux-contline) / medflux
    tpeak = flaretime[np.argmax(flareflux-contline)]

    p05 = np.where((flareflux-contline <= ampl*0.5))
    if len(p05[0]) == 0:
        fwhm = dur0 * 0.25
        # print('> warning') # % ;
    else:
        fwhm = np.max(flaretime[p05]) - np.min(flaretime[p05])

    # fit flare with single aflare model
    pguess = (tpeak, fwhm, ampl)
    # print(pguess) # % ;
    # print(len(flaretime)) # % ;

    try:
        popt1, pcov = curve_fit(aflare1, np.array(flaretime), (flareflux-contline) / medflux, p0=pguess)
    except ValueError:
        # tried to fit bad data, so just fill in with NaN's
        # shouldn't happen often
        popt1 = np.array([np.nan, np.nan, np.nan])
    except RuntimeError:
        # could not converge on a fit with aflare
        # fill with bad flag values
        popt1 = np.array([-99., -99., -99.])

    # flare_chisq = total( flareflux - modelflux)**2.  / total(error)**2
    flare_chisq = chisq(flareflux, flareerror, modelflux)

    # measure KS stats of flare versus model
    ks_d, ks_p = stats.ks_2samp(flareflux, modelflux)

    # measure KS stats of flare versus continuum regions
    ks_dc, ks_pc = stats.ks_2samp(flareflux-contline, contflux-np.polyval(contfit, conttime))

    # put flux in relative units, remove dependence on brightness of stars
    # rel_flux = (flux_gap - flux_model) / np.median(flux_model)
    # rel_error = error / np.median(flux_model)

    # measure flare ED
    ed = EquivDur(np.array(flaretime), (flareflux-contline)/medflux)

    # output a dict or array?
    params = np.array((tstart, tstop, tpeak, ampl, fwhm, dur0,
                       popt1[0], popt1[1], popt1[2],
                       flare_chisq, ks_d, ks_p, ks_dc, ks_pc, ed), dtype='float')
    # the parameter names for later reference
    header = ['t_start', 't_stop', 't_peak', 'amplitude', 'FWHM', 'duration',
             't_peak_aflare1', 't_FWHM_aflare1', 'amplitude_aflare1',
             'flare_chisq', 'KS_d_model', 'KS_p_model', 'KS_d_cont', 'KS_p_cont', 'Equiv_Dur']

    if ReturnHeader is True:
        return header
    else:
        return params


def MeasureS2N(flux, error, model, istart=-1, istop=-1):
    '''
    this MAY NOT be something i want....
    '''
    if (istart < 0):
        istart = 0
    if (istop < 0):
        istop = len(flux)

    flareflux = flux[istart:istop+1]
    modelflux = model[istart:istop+1]

    s2n = np.sum(np.sqrt((flareflux) / (flareflux + modelflux)))
    return s2n


def FlarePer(time, minper=0.1, maxper=30.0, nper=20000):
    '''
    Look for periodicity in the flare occurrence times. Could be due to:
    a) mis-identified periodic things (e.g. heartbeat stars)
    b) crazy binary star flaring things
    c) flares on a rotating star
    d) bugs in code
    e) aliens

    '''

    # use energy = 1 for flare times.
    # This will create something like the window function
    energy = np.ones_like(time)

    # Use Jake Vanderplas faster version!
    pgram = LombScargleFast(fit_offset=False)
    pgram.optimizer.set(period_range=(minper,maxper))

    pgram = pgram.fit(time, energy - np.nanmedian(energy))

    df = (1./minper - 1./maxper) / nper
    f0 = 1./maxper
    pwr = pgram.score_frequency_grid(f0, df, nper)

    freq = f0 + df * np.arange(nper)
    per = 1./freq

    pk = per[np.argmax(pwr)] # peak period
    pp = np.max(pwr) # peak period power

    return pk, pp


def MultiFind(time, flux, error, flags, mode=3,
              gapwindow=0.1, minsep=3, debug=False):
    '''
    this needs to be either
    1. made in to simple multi-pass cleaner,
    2. made in to "run till no signif change" cleaner, or
    3. folded back in to main code
    '''


    # the bad data points (search where bad < 1)
    bad = FlagCuts(flags, returngood=False)
    flux_i = np.copy(flux)

    if (mode == 1):
        # just use the multi-pass boxcar and average. Simple. Too simple...
        flux_model1 = detrend.MultiBoxcar(time, flux, error, kernel=0.1)
        flux_model2 = detrend.MultiBoxcar(time, flux, error, kernel=1.0)
        flux_model3 = detrend.MultiBoxcar(time, flux, error, kernel=10.0)

        flux_model = (flux_model1 + flux_model2 + flux_model3) / 3.
        flux_diff = flux - flux_model

    if (mode == 2):
        # first do a pass thru w/ largebox to get obvious flares
        box1 = detrend.MultiBoxcar(time, flux_i, error, kernel=2.0, numpass=2)
        sin1 = detrend.FitSin(time, box1, error, maxnum=2, maxper=(max(time)-min(time)))

        box2 = detrend.MultiBoxcar(time, flux_i - sin1, error, kernel=0.25)
        flux_model = (box2 + sin1)
        flux_diff = flux - flux_model


    if (mode == 3):
        # do iterative rejection and spline fit - like FBEYE did
        # also like DFM & Hogg suggest w/ BART

        box1 = detrend.MultiBoxcar(time, flux, error, kernel=2.0, numpass=2)
        sin1 = detrend.FitSin(time, box1, error, maxnum=5, maxper=(max(time)-min(time)),
                              per2=False, debug=debug)
        # sin1 = detrend.FitMedSin(time, box1, error)
        box3 = detrend.MultiBoxcar(time, flux - sin1, error, kernel=0.3)
        t = np.array(time)
        dt = np.nanmedian(t[1:] - t[0:-1])
        print(dt)
        exptime_m = (np.nanmax(time) - np.nanmin(time)) / len(time)
        # ksep used to = 0.07...
        flux_model = detrend.IRLSSpline(time, box3, error, numpass=20, debug=debug, ksep=exptime_m*10.) + sin1

        signalfwhm = dt * 2
        ftime = np.arange(0, 2, dt)
        modelfilter = aflare1(ftime, 1, signalfwhm, 1)
        flux_diff = signal.correlate(flux - flux_model, modelfilter, mode='same')

    if (mode == 4):
        # fit data with a SAVGOL filter
        dt = np.nanmedian(time[1:] - time[0:-1])
        Nsmo = np.floor(0.2 / dt)
        if Nsmo % 2 == 0:
            Nsmo = Nsmo + 1
        flux_model = savgol_filter(flux, Nsmo, 2, mode='nearest')
        flux_diff = flux - flux_model


    # run final flare-find on DATA - MODEL
    isflare = FINDflare(flux_diff, error, N1=3, N3=3,
                        returnbinary=True, avg_std=True)


    # now pick out final flare candidate points from above
    cand1 = np.where((bad < 1) & (isflare > 0))[0]

    x1 = np.where((np.abs(time[cand1]-time[-1]) < gapwindow))
    x2 = np.where((np.abs(time[cand1]-time[0]) < gapwindow))
    cand1 = np.delete(cand1, x1)
    cand1 = np.delete(cand1, x2)

    # print(len(cand1))
    if (len(cand1) < 1):
        istart = np.array([])
        istop = np.array([])
    else:
        # find start and stop index, combine neighboring candidates in to same events
        istart = cand1[np.append([0], np.where((cand1[1:]-cand1[:-1] > minsep))[0]+1)]
        istop = cand1[np.append(np.where((cand1[1:]-cand1[:-1] > minsep))[0], [len(cand1)-1])]

    # if start & stop times are the same, add 1 more datum on the end
    to1 = np.where((istart-istop == 0))
    if len(to1[0])>0:
        istop[to1] += 1

    if debug is True:
        plt.figure()
        plt.title('debugging plot')
        plt.scatter(time, flux, alpha=0.5,label='flux')
        plt.plot(time,flux_model, c='black',label='flux model')
        plt.scatter(time[cand1], flux[cand1], c='red',label='flare candidates')
        plt.legend()
        plt.show()
        plt.close()

    # print(istart, len(istart))
    return istart, istop, flux_model


# sketching some baby step ideas w/a matched filter
'''
def MatchedFilterFind(time, flux, signalfwhm=0.01):
    dt = np.nanmedian(time[1:] - time[0:-1])
    ftime = np.arange(0, 2, dt)
    modelfilter = aflare1(ftime, 1, signalfwhm, 1)

    corr = signal.correlate(flux-np.nanmedian(flux), modelfilter, mode='same')

    plt.figure()
    plt.plot(time, flux - np.nanmedian(flux))
    plt.plot(time, corr,'red')
    plt.show()

    return
'''


def FakeFlares(time, flux, error, flags, tstart, tstop,
               nfake=100, npass=1, ampl=(0.1,100), dur=(0.5,60),
               outfile='', savefile=False, gapwindow=0.1,
               verboseout=False, display=False, debug=False):
    '''
    Create nfake number of events, inject them in to data
    Use grid of amplitudes and durations, keep ampl in relative flux units
    Keep track of energy in Equiv Dur

    duration defined in minutes
    amplitude defined multiples of the median error

    still need to implement npass, to re-do whole thing and average results
    '''

    # QUESTION: how many fake flares can I inject at once?
    # i.e. can I get away with doing fewer re-runs with more flares injected?

    std = np.nanmedian(error)

    ampl_fake = (np.random.random(nfake) * (ampl[1] - ampl[0]) + ampl[0]) * std
    dur_fake =  (np.random.random(nfake) * (dur[1] - dur[0]) + dur[0]) / 60. / 24.

    t0_fake = np.zeros(nfake, dtype='float')
    s2n_fake = np.zeros(nfake, dtype='float')
    ed_fake = np.zeros(nfake, dtype='float')

    new_flux = np.array(flux)#, copy=True)
    time = np.array(time)
    error = np.array(error) 
    flags = np.array(flags)
    
    for k in range(nfake):
        # generate random peak time, avoid known flares
        isok = False
        while isok is False:
            # choose a random peak time
            t0 =  np.random.choice(time)

            if len(tstart)>0:
                x = np.where((t0 >= tstart) & (t0 <= tstop))
                if (len(x[0]) < 1):
                    isok = True
            else:
                isok = True

        t0_fake[k] = t0

        # generate the fake flare
        fl_flux = aflare1(time, t0, dur_fake[k], ampl_fake[k])

        # in_fl = np.where((time >= t0-dur_fake[k]) & (time <= t0 + 3.0*dur_fake[k]))

        s2n_fake[k] = np.sqrt( np.sum((fl_flux**2.0) / (std**2.0)) )
        ed_fake[k] = EquivDur(time, fl_flux)

        # inject flare in to light curve
        new_flux = new_flux + fl_flux

    '''
    Re-run flare finding for data + fake flares
    Figure out: which flares were recovered?
    '''

    # all the hard decision making should go here
    istart, istop, flux_model = MultiFind(time, new_flux, error, flags, gapwindow=gapwindow, debug=debug)

    rec_fake = np.zeros(nfake)

    if len(istart)>0: # in case no flares are recovered, even after injection
        for k in range(nfake): # go thru all recovered flares
            # do any injected flares overlap recovered flares?
            rec = np.where((t0_fake[k] >= time[istart]) &
                           (t0_fake[k] <= time[istop]))

            if (len(rec[0]) > 0):
                rec_fake[k] = 1

    # nbins = int(nfake/10.)
    # if nbins < 10:
    #     nbins = 10
    nbins = 20

    # the number of events per bin recovered
    rec_bin_N, ed_bin = np.histogram(ed_fake, weights=rec_fake, bins=nbins)
    # the number of events per bin
    rec_bin_D, _ = np.histogram(ed_fake, bins=nbins)

    ed_bin_center = (ed_bin[1:] + ed_bin[:-1])/2.

    rec_bin = rec_bin_N / rec_bin_D

    if savefile is True:
        # look to see if output folder exists
        # fldr = objectid[0:3]
        # outdir = 'aprun/' + fldr + '/'
        # if not os.path.isdir(outdir):
        #     try:
        #         os.makedirs(outdir)
        #     except OSError:
        #         pass
        
        header = ['min_time','max_time','std_dev','nfake',
                  'min_amplitude','max_amplitude',
                  'min_duration','max_duration',
                  'ed68_i','ed90_i',
                 ]

        if glob.glob(outfile)==[]:
            dfout = pd.DataFrame()
            metadata = dict()
        else:
            dfout, metadata = h5load(pd.HDFStore(outfile))
        
        # use this completeness curve to estimate 68% complete
        rl = np.isfinite(rec_bin)
        w_in = rec_bin[rl]
        frac_rec_sm = wiener(w_in, 3)
        x68 = np.where((frac_rec_sm >= 0.68))
        if len(x68[0])>0:
            ed68_i = min(ed_bin_center[rl][x68])
        else:
            ed68_i = -99

        x90 = np.where((frac_rec_sm >= 0.90))
        if len(x90[0])>0:
            ed90_i = min(ed_bin_center[rl][x90])
        else:
            ed90_i = -99

        outrow = [[item] for item in [min(time), max(time), std, nfake, ampl[0], 
                  ampl[1], dur[0], dur[1], ed68_i, ed90_i, 
                  np.nan, np.nan, np.nan,]]
        
        if verboseout is True:
            for i in range(len(w_in)-1):
                outstring = outstring + \
                            str(ed_bin_center[rl][i]) + ', ' + \
                            str(rec_bin[rl][i]) + ', ' + \
                            str(frac_rec_sm[i]) + '\n'
                header = header + ['ed_bin_center','rec_bin','frac_rec_sm']
                outrow = outrow+[ed_bin_center[rl][i],rec_bin[rl][i],frac_rec_sm[i]]
                dfout = dfout.append(pd.DataFrame(dict(zip(header,outrow))),ignore_index=True)
        else:
            dfout = dfout.append(pd.DataFrame(dict(zip(header,outrow))),ignore_index=True)

        h5store(outfile,dfout,**metadata)


    return ed_bin_center, rec_bin


# objectid = '9726699'  # GJ 1243
def RunLC(file='', objectid='', ftype='sap', lctype='',
          display=False, readfile=False, debug=False, dofake=True,
          dbmode='fits', gapwindow=0.1, maxgap=0.125, verbosefake=False, nfake=100):
    '''
    Main wrapper to obtain and process a light curve
    '''


    # pick and process a totally random LC.
    # important for reality checking!
    if (objectid is 'random'):
        obj, num = np.loadtxt('get_objects.out', skiprows=1, unpack=True, dtype='str')
        rand_id = int(np.random.random() * len(obj))
        objectid = obj[rand_id]
        print('Random ObjectID Selected: ' + objectid)

    # get the data
    if debug is True:
        print(str(datetime.datetime.now()) + ' GetLC started')
        print(file, objectid)

    #---------------------------------------------------
    if dbmode is 'mysql':
        data_raw = GetLCdb(objectid, readfile=readfile, type=lctype, onecadence=False)
        data = OneCadence(data_raw)

        # data columns are:
        # QUARTER, TIME, PDCFLUX, PDCFLUX_ERR, SAP_QUALITY, LCFLAG, SAPFLUX, SAPFLUX_ERR

        qtr = data[:,0]
        time = data[:,1]
        lcflag = data[:,4] # actual SAP_QUALITY

        exptime = data[:,5] # actually the LCFLAG
        exptime[np.where((exptime < 1))] = 54.2 / 60. / 60. / 24.
        exptime[np.where((exptime > 0))] = 30 * 54.2 / 60. / 60. / 24.

        if ftype == 'sap':
            flux_raw = data[:,6]
            error = data[:,7]
        else: # for PDC data
            flux_raw = data[:,2]
            error = data[:,3]

        # put flare output in to a set of subdirectories.
        # use first 3 digits to help keep directories to ~1k files
        fldr = objectid[0:3]
        outdir = 'aprun/' + fldr + '/'
        if not os.path.isdir(outdir):
            try:
                os.makedirs(outdir)
            except OSError:
                pass
        # open the output file to store data on every flare recovered
        outfile = outdir + objectid

    elif dbmode in ('txt','ktwo','everest','vdb','csv','fits'):
        outfile, objectid, qtr, time, lcflag, exptime, flux_raw, error = Get(dbmode,file, objectid)
    
    #-----------------------------------------------

    if debug is True:
        print('outfile = ' + outfile)

    ### Basic flattening
    # flatten quarters with polymonial
    flux_qtr = detrend.QtrFlat(time, flux_raw, qtr)

    # then flatten between gaps
    flux_gap = detrend.GapFlat(time, flux_qtr, maxgap=maxgap)

    _, dl, dr = detrend.FindGaps(time, maxgap=maxgap)
    if debug is True:
        print("dl")
        print(dl)
        print("dr")
        print(dr)

    # uQtr = np.unique(qtr)
    istart = np.array([], dtype='int')
    istop = np.array([], dtype='int')
    ed68 = []
    ed90 = []
    flux_model = np.zeros_like(flux_gap)

    for i in range(0, len(dl)):
        # detect flares in this gap
        if debug is True:
            print(i, str(datetime.datetime.now()) + ' MultiFind started')

        istart_i, istop_i, flux_model_i = MultiFind(time[dl[i]:dr[i]], flux_gap[dl[i]:dr[i]],
                                                    error[dl[i]:dr[i]], lcflag[dl[i]:dr[i]],
                                                    gapwindow=gapwindow, debug=debug)

        # run artificial flare test in this gap
        if debug is True:
            print(str(datetime.datetime.now()) + ' FakeFlares started')

        if dofake is True:
            medflux = np.nanmedian(flux_model_i) # flux needs to be normalized

            if len(istart_i)>0:
                t_tmp1 = time[dl[i]:dr[i]][istart_i]
                t_tmp2 = time[dl[i]:dr[i]][istop_i]
            else:
                t_tmp1 = []
                t_tmp2 = []
            ed_fake, frac_rec = FakeFlares(time[dl[i]:dr[i]], flux_gap[dl[i]:dr[i]]/medflux - 1.0,
                                           error[dl[i]:dr[i]]/medflux, lcflag[dl[i]:dr[i]],
                                           t_tmp1, t_tmp2,
                                           savefile=True, verboseout=verbosefake, gapwindow=gapwindow,
                                           outfile=outfile + '_fake.h5', display=display,
                                           nfake=nfake, debug=debug)

            rl = np.isfinite(frac_rec)
            frac_rec_sm = wiener(frac_rec[rl], 3)

            # use this completeness curve to estimate 68% complete
            x68 = np.where((frac_rec_sm >= 0.68))
            if len(x68[0])>0:
                ed68_i = min(ed_fake[rl][x68])
            else:
                ed68_i = -99

            x90 = np.where((frac_rec_sm >= 0.90))
            if len(x90[0])>0:
                ed90_i = min(ed_fake[rl][x90])
            else:
                ed90_i = -99

            if display is True:
                # print(np.shape(ed_fake), np.shape(frac_rec), np.shape(rl), np.shape(frac_rec_sm))
                plt.figure()
                plt.plot(ed_fake, frac_rec, c='k')
                plt.plot(ed_fake[rl], frac_rec_sm, c='red', linestyle='dashed', lw=2)
                plt.vlines([ed68_i, ed90_i], ymin=0, ymax=1, colors='b',alpha=0.75, lw=5)
                plt.xlabel('Flare Equivalent Duration (seconds)')
                plt.ylabel('Fraction of Recovered Flares')

                plt.xlim((0,np.nanmax(ed_fake)))
                plt.savefig(file + '_fake_recovered.pdf',dpi=300, bbox_inches='tight', pad_inches=0.5)
                plt.show()
        else:
            # for speed you can skip the fake-flare tests
            ed68_i = -199
            ed90_i = -199

        ed68 = np.append(ed68, np.zeros(len(istart_i)) + ed68_i)
        ed90 = np.append(ed90, np.zeros(len(istart_i)) + ed90_i)

        istart = np.array(np.append(istart, istart_i + dl[i]), dtype='int')
        istop = np.array(np.append(istop, istop_i + dl[i]), dtype='int')

        flux_model[dl[i]:dr[i]] = flux_model_i

        # look at the completeness curve
        # plt.figure()
        # plt.plot(ed_fake, frac_rec)
        # plt.scatter(ed_fake[rl], frac_rec[rl])
        # # plt.plot(ed_fake[rl], savgol_filter(frac_rec[rl], 3, 2))
        # plt.plot(ed_fake[rl], wiener(frac_rec[rl], 3))
        # plt.xlabel('Equiv Dur of simulated flares')
        # plt.ylabel('Fraction Recovered')
        # plt.show()

    '''
    ### MY FIRST ATTEMPT AT FLARE FINDING
    # fit sin curves
    flux_sin = detrend.FitSin(time, flux_gap, error)

    # run iterative boxcar over data
    flux_smo = detrend.MultiBoxcar(time, flux_gap - flux_sin, error)

    flux_model = flux_sin + flux_smo
    # flux_model = flux_sin + detrend.MultiBoxcar(time, flux_smo, error) # double detrend?

    istart, istop = DetectCandidate(time, flux_gap, error, lcflag, flux_model)
    '''

    # print(istart)

    if display is True:
        print(str(len(istart))+' flare candidates found')

        plt.figure()
        plt.plot(time, flux_gap, 'k', alpha=0.7, lw=0.8)

        # for g in dl:
        #     plt.scatter(time[g], flux_gap[g], color='blue', marker='v',s=40)

        # plt.scatter(time[istart], flux_gap[istart], color='red', marker='*',s=90)
        # plt.scatter(time[istop], flux_gap[istop], color='orange', marker='p',s=60, alpha=0.5)
        for g in range(len(istart)):
            plt.plot(time[istart[g]:istop[g]+1],
                     flux_gap[istart[g]:istop[g]+1],color='red', lw=1)

        plt.plot(time, flux_model, 'blue', lw=3, alpha=0.7)


        plt.xlabel('Time (BJD - 2454833 days)')
        plt.ylabel(r'Flux (e- sec$^{-1}$)')

        xdur = np.nanmax(time) - np.nanmin(time)
        xdur0 = np.nanmin(time) + xdur/2.
        xdur1 = np.nanmin(time) + xdur/2. + 2.6
        plt.xlim(xdur0, xdur1) # only plot a chunk of the data

        xdurok = np.where((time >= xdur0) & (time <= xdur1))
        if len(xdurok[0])>0:
            yminmax = [np.nanmin(flux_gap[xdurok]), np.nanmax(flux_gap[xdurok])]
            if sum(np.isfinite(yminmax)) > 1:
                plt.ylim(yminmax[0], yminmax[1])

        plt.savefig(file + '_lightcurve.pdf', dpi=300, bbox_inches='tight', pad_inches=0.5)
        plt.show()

    '''
    #-- IF YOU WANT TO PLAY WITH THE WAVELET STUFF MORE, WORK HERE
    test_model = detrend.WaveletSmooth(time, flux_gap)
    test_cand = DetectCandidate(time, flux_gap, error, test_model)

    print(len(cand))
    print(len(test_cand))

    if display is True:
        plt.figure()
        plt.plot(time, flux_gap, 'k')
        plt.plot(time, test_model, 'green')

        plt.scatter(time[test_cand], flux_gap[test_cand], color='orange', marker='p',s=60, alpha=0.8)
        plt.show()
    '''

    # set this to silence bad fit warnings from polyfit
    warnings.simplefilter('ignore', np.RankWarning)
    
    metadata = {'ObjectID' : objectid,
                 'File' : file,
                 'Date-Run' : str(datetime.datetime.now()),
                 'Appaloosa-Version': __version__,
                 'N_epoch in LC' : str(len(time)),
                 'Total exp time of LC' : str(np.sum(exptime)),
                 }

    if debug is True:
        print(str(datetime.datetime.now()) + 'Getting output header')
        
    header = FlareStats(time, flux_gap, error, flux_model,
                        ReturnHeader=True)
    header = header + ['ED68i','ED90i']
    dfout = pd.DataFrame()
    
    if debug is True:
        print(str(datetime.datetime.now()) + 'Getting FlareStats')
        
    # loop over EACH FLARE, compute stats
    for i in range(0,len(istart)):
        stats_i = FlareStats(time, flux_gap, error, flux_model,
                             istart=istart[i], istop=istop[i])
        _ = [[item] for item in [*stats_i,ed68[i],ed90[i]]]
        dfout = dfout.append(pd.DataFrame(dict(zip(header,_))),
                             ignore_index=True)
        
    h5store(outfile + '_flare.h5',dfout,**metadata)
    return

#Use h5 to store metadata and data such that it is easy to propagate, 
#found here: https://stackoverflow.com/a/29130146
#originally from Pandas Cookbook

def h5store(filename, df, **kwargs):
    store = pd.HDFStore(filename)
    store.put('mydata', df)
    store.get_storer('mydata').attrs.metadata = kwargs
    store.close()
    return

def h5load(store):
    data = store['mydata']
    metadata = store.get_storer('mydata').attrs.metadata
    store.close()
    return data, metadata


# let this file be called from the terminal directly. e.g.:
# $python appaloosa.py 12345678
if __name__ == "__main__":
    import sys
    RunLC(file=str(sys.argv[1]), dbmode='fits', display=True, debug=True, nfake=100)

