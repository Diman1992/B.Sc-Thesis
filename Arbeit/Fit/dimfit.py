#-*- coding: iso-8859-1 -*-

# ==================================================
#	import modules
# ==================================================

import inspect
import sys

import evaluation as ev
import evaluation.simpleplot as sp
import latextable as lt

import iminuit as minuit

# calc with uncertainties and arrays of uncertainties [[val, std], ...]
import uncertainties as uc
import uncertainties.unumpy as unp

# calc with arrays
import numpy as np

# plot engine
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

# for fitting curves
from scipy import integrate
from scipy import optimize

# constants
import scipy.constants as const

# ==================================================
#	settings
# ==================================================


sp.params["text.latex.preamble"] = sp.tex_mathpazo_preamble
plt.rcParams.update(sp.params)

# ==================================================
#	function to print equations with
#	matplotlib
# ==================================================


def show(x) :
    assert isinstance(x, str)
    print x + " = "
    print eval(x)
    print "\n"

def print_tex(s):
    assert isinstance(s, str)
    fig = plt.figure()
    ax = fig.add_subplot(111)
    ax.axis('off')
    ax.text(0.0, 0.5,
            s.replace('\t',' ').replace('\n',' ').replace('%',''),
            color='k', size = 'x-large'
    )
    plt.show()

###########################################################################
#                                 Beginn                                  #
###########################################################################


# ===== Hilfsfunktionen ============================

def fillto(array, n):
    """ helper for diagonal matrices,
    fills zeros in front of array till size n.
    """
    s = len(array)
    if s < n:
        return np.append([np.zeros(n-s)], [array])
    else:
        return array

def get_full_matrix(m):
    """create full matrix just with diagonal elements."""
    size = max([len(i) for i in m]) + 1

    r = np.array([fillto(s, size) for s in m])
    r = np.append(r, [np.zeros(size)], axis=0)

    return np.matrix(r + r.T + np.eye(size))


# ==================================================
#	Konstanten
# ==================================================

# CKM-Element
V_cs = uc.ufloat(0.985, 0.0010)

# Fermi-Konstante
# Einheit GeV^-2
fermi = 1.16637 * 1e-5

# 1 / eV = 6.58212*1e-16 s in natürlichen Einheiten -> Faktor für 1/s -> eV
invsec_to_nat = 6.58212 * 1e-16


###########################################################################
#                            Datenbearbeitung                             #
###########################################################################


def get_nom(dG):
    dG_m = np.array([uc.ufloat_fromstr(s) for s in dG])
    dG_m = unp.nominal_values(dG_m)
    dG_m = invsec_to_nat * dG_m

    return dG_m


def MeV2GeV(m):
    return m * 1e-3


def get_sigma(dG, cor):
    return dG * cor * 0.01


def get_cov(sigma, cor):
    size = len(sigma)
    cov = np.zeros((size, size))

    for i in range(size):
        for j in range(size):
            cov[i,j] = sigma[i] * sigma[j] * cor[i,j]

    return cov

def get_invcov(dG, stat_sigma_cor, sys_sigma_cor, stat_cor, sys_cor):
    stat_sigma = get_sigma(dG, stat_sigma_cor)
    sys_sigma = get_sigma(dG, sys_sigma_cor)

    stat_cov = get_cov(stat_sigma, stat_cor)
    sys_cov = get_cov(sys_sigma, sys_cor)

    return np.linalg.inv((stat_cov + sys_cov))


###########################################################################
#                               Formfaktor                                #
###########################################################################


def l(a, b, c):
    return (a - b - c)**2 - 4*b*c


def p(q, m_P, m_D):
    return np.sqrt(l(m_P**2, m_D**2, q)) / (2.0 * m_P)


def z(q, m_P, m_D, t0):
    t_plus = (m_P + m_D)**2

    a = np.sqrt(t_plus - q)
    b = np.sqrt(t_plus - t0)

    return (a - b) / (a + b)


def phi(q, m_P, m_D, t0):
    m_c = 1.275 # 0.025
    t_minus = (m_P - m_D)**2
    t_plus = (m_P + m_D)**2

    # Ausnahmebehandlung für q = 0!
    if isinstance(q, np.ndarray):
        critical_val = np.zeros(len(q))
        for i in range(len(q)):
            if q[i] == 0.0:
                critical_val[i] = 1.0 / (4.0 * t_plus)
            else:
                critical_val[i] = z(q[i], m_P, m_D, 0.0) / (-q[i])
    else:
        if q == 0.0:
            critical_val = 1.0 / (4.0 * t_plus)
        else:
            critical_val = z(q, m_P, m_D, 0.0) / (-q)

    a = critical_val**(5.0/2.0)
    b = (z(q, m_P, m_D, t0) / (t0 - q))**(-1.0/2.0)
    c = (z(q, m_P, m_D, t_minus) / (t_minus - q))**(-3.0/4.0)
    d = (t_plus - q) / (t_plus - t0)**(1.0/4.0)

    return np.sqrt(np.pi * m_c**2 / 3.0) * a * b * c * d


def P(q, m_P, m_D, m_Pr):
    return z(q, m_P, m_D, m_Pr**2)
    # return 1.0


def formfactor(q, a, m_P, m_D, m_Pr, t0):
    #prefactor = 1.0 / (P(q, m_P, m_D, m_Pr) * phi(q, m_P, m_D, t0))
    prefactor = 1.0 / (1 - q / m_Pr**2)
    return prefactor * np.polynomial.polynomial.polyval(z(q, m_P, m_D, t0), a)


def diff_gamma_theo(q, a, m_P, m_D, m_Pr, t0):
    if np.isnan(p(q, m_P, m_D)):
        print q, m_P, m_D
        print p(q, m_P, m_D)
        sys.exit(1)
    prefactor = fermi**2 / (24.0 * np.pi**3) * p(q, m_P, m_D)**3
    return prefactor * formfactor(q, a, m_P, m_D, m_Pr, t0)**2


###########################################################################
#                                   Fit                                   #
###########################################################################


def integrate_gamma(q_bin, *args):
    """
    args: a, m_P, m_D, m_Pr, t0
    """
    return integrate.quad(diff_gamma_theo, q_bin[0], q_bin[1], args)


def chi2(q_bins, dG, m_P, m_D, m_Pr, t0, a0, a1, a2):

    bin_number = len(q_bins)
    a = [a0, a1, a2]

    c2 = 0.0

    for i in range(bin_number):
        for j in range(bin_number):
            dG_th_i = integrate_gamma(q_bins[i], a, m_P, m_D, m_Pr, t0)[0]
            dG_th_j = integrate_gamma(q_bins[j], a, m_P, m_D, m_Pr, t0)[0]

            term_i = dG[i] - dG_th_i
            term_j = dG[j] - dG_th_j

            c2 += term_i * cov_inv[i,j] * term_j

    return c2


class generic_chi2:
    def __init__(self, chi2, q_bins, dG, m_P, m_D, m_Pr, t0, num_var):
        self.chi2 = chi2
        self.q_bins = q_bins
        self.dG = dG
        self.m_P = m_P
        self.m_D = m_D
        self.m_Pr = m_Pr
        self.t0 = t0
        self.num_var = num_var

        args = minuit.describe(chi2)
        self.func_code = minuit.Struct(
                co_varnames = args[len(args)-self.num_var:],
                co_argcount = len(args)-self.num_var
            )
    def __call__(self, *arg):
        return self.chi2(
                self.q_bins,
                self.dG,
                self.m_P,
                self.m_D,
                self.m_Pr,
                self.t0,
                *arg
            )


def fit_form(chi2, q_bins, dG, m_P, m_D, m_Pr, t0, **kwds):
    num_var = len(
        [True for var in inspect.getargspec(chi2)[0] if var.startswith("a")]
    )

    m = minuit.Minuit(
        generic_chi2(chi2, q_bins, dG, m_P, m_D, m_Pr, t0, num_var),
        **kwds
    )
    m.migrad()
    m.hesse()

    return m


def plot_fittedform(m, m_P, m_D, m_Pr, t0):
    a_keys = m.values.keys()
    a_keys.sort()
    a = np.array([m.values[a_keys[i]] for i in range(len(a_keys))])

    err_keys = m.errors.keys()
    err_keys.sort()
    err = np.array([m.errors[a_keys[i]] for i in range(len(err_keys))])

    # a = np.array([m.values["a0"], m.values["a1"], m.values["a2"]])
    # err = np.array([m.errors["a0"], m.errors["a1"], m.errors["a2"]])

    q = np.linspace(0.0, 2.0, 100)
    form = formfactor(q, a, m_P, m_D, m_Pr, t0)

    fig1 = plt.figure()
    ax1 = fig1.add_subplot(111)

    err_min = formfactor(q, a - err, m_P, m_D, m_Pr, t0)
    err_max = formfactor(q, a + err, m_P, m_D, m_Pr, t0)

    ax1.plot(q, form, "-", label="Fit")
    ax1.fill_between(
        q,
        err_min,
        err_max,
        alpha=0.2
    )

    ax1.set_xlabel(r'$q^2 / \unit{GeV^2}$')
    ax1.set_ylabel(r'$f_+(q^2)$')

    ax1.legend(loc = 'best')
    ax1 = ev.plot_layout(ax1)

    fig1.tight_layout()
    plt.show()


def plot_form(a, m_P, m_D, m_Pr, t0, err=None):

    q = np.linspace(0.0, 2.0, 100)
    form = formfactor(q, a, m_P, m_D, m_Pr, t0)

    fig1 = plt.figure()
    ax1 = fig1.add_subplot(111)

    ax1.plot(q, form, "-", label="Fit")

    if err != None:
        err_min = formfactor(q, a - err, m_P, m_D, m_Pr, t0)
        err_max = formfactor(q, a + err, m_P, m_D, m_Pr, t0)

        ax1.fill_between(
            q,
            err_min,
            err_max,
            alpha=0.2
        )

    ax1.set_xlabel(r'$q^2 / \unit{GeV^2}$')
    ax1.set_ylabel(r'$f_+(q^2)$')

    ax1.legend(loc = 'best')
    ax1 = ev.plot_layout(ax1)

    fig1.tight_layout()
    plt.show()

###########################################################################
#                               Auswertung                                #
###########################################################################



# m_K = 493.677 # MeV / c**2
# m_D = 1968.49 # MeV / c**2
# m_Dr = 2112.3 # MeV / c**2

# Massen
m_P0 = MeV2GeV(134.9766)
m_Pm = MeV2GeV(139.57018)
m_Dr = MeV2GeV(2010.27)

m_c = 1.275 # 0.025
m_D = MeV2GeV(1869.62) # 0.15 MeV
m_D0 = MeV2GeV(1865)
m_Ds = MeV2GeV(1968.49) # 0.32 MeV
m_K0 = MeV2GeV(497.614) # 0.024 MeV
m_Km = MeV2GeV(493.677) # 0.016 MeV


# Einheit GeV**2 / c**4
q_bins = [
        [0.0, 0.2],
        [0.2, 0.4],
        [0.4, 0.6],
        [0.9, 0.8],
        [1.2, 1.0],
        [1.5, 1.2],
        [2.0, 1.4],
	[1.4, 1.6],
	[1.6, 1.8]
]

# Parameter für z-Entwicklung
t_minus = (m_D - m_K0)**2
t_plus = (m_D + m_K0)**2
t0 = t_plus * (1.0 - np.sqrt(1.0 - t_minus / t_plus))

# ==================================================
#	D0 -> K-
# ==================================================


# Einheit 1/ns
dG = [
    "17.79(47)",
    "15.62(45)",
    "14.02(43)",
    "12.28(40)",
    "8.92(34)",
    "8.17(32)",
    "4.96(25)",
    "2.67(18)",
    "1.19(13)"
]

# Korrelationsmatrizen
stat_cor_dG_m = [
    [-0.050, 0.001, 0.000, 0.000, 0.000, 0.000, 0.000, 0.000],
    [-0.070, 0.001, 0.000, 0.000, 0.000, 0.000, 0.000],
    [-0.070, 0.000, 0.000, 0.000, 0.000, 0.000],
    [-0.070, 0.000, 0.000, 0.000, 0.000],
    [-0.060, 0.000, 0.000, 0.000],
    [-0.060, 0.000, 0.000],
    [-0.050, 0.000],
    [-0.040]
]

sys_cor_dG_m = [
    [0.80, 0.87, 0.78, 0.79, 0.56, 0.81, 0.51, 0.03],
    [0.97, 0.98, 0.96, 0.92, 0.92, 0.87, 0.57],
    [0.97, 0.96, 0.86, 0.94, 0.82, 0.45],
    [0.98, 0.94, 0.94, 0.90, 0.59],
    [0.94, 0.97, 0.90, 0.57],
    [0.89, 0.97, 0.80],
    [0.88, 0.49],
    [0.82], 
]

stat_cor_dG_m =  get_full_matrix(stat_cor_dG_m)
sys_cor_dG_m =  get_full_matrix(sys_cor_dG_m)

stat_sigma = [2.03, 2.19, 2.31, 2.47, 2.73, 3.14, 3.63, 4.90, 8.43]
sys_sigma = [1.26, 1.10, 1.12, 1.15, 1.20, 1.36, 1.35, 1.63, 2.55]



dG = get_nom(dG)

cov_inv = get_invcov(
    dG,
    stat_sigma,
    sys_sigma,
    stat_cor_dG_m,
    sys_cor_dG_m
)

m = fit_form(
    chi2,
    q_bins,
    dG,
    m_D,
    m_Pm,
    m_Dr,
    t0,
    a0=0.01,
    a1=-0.1,
    a2=0.1,
    # error_a0=0.1,
    # error_a1=0.1,
    # error_a2=0.01,
    # errordef=0.01
)

plot_fittedform(m, m_D, m_Pm, m_Dr, t0)

f0 = formfactor(
    0.0,
    [m.values["a0"], m.values["a1"], m.values["a2"]],
    m_D,
    m_Pm,
    m_Dr,
    t0
)

# plot_form([0.01, -0.1, 0.1], m_D, m_Pm, m_Dr, t0)

print "f(0)|V_cd| = {:0.4f}".format(f0)



# ==================================================
#	D+ -> K0
# ==================================================
# Einheit 1/ns
dG = [
    "17.79(47)",
    "15.62(45)",
    "14.02(43)",
    "12.28(40)",
    "8.92(34)",
    "8.17(32)",
    "4.96(25)",
    "2.67(18)",
    "1.19(13)"
]

# Korrelationsmatrizen
stat_cor_dG_m = [
    [-0.050, 0.001, 0.000, 0.000, 0.000, 0.000, 0.000, 0.000],
    [-0.070, 0.001, 0.000, 0.000, 0.000, 0.000, 0.000],
    [-0.070, 0.001, 0.000, 0.000, 0.000, 0.000],
    [-0.070, 0.000, 0.000, 0.000, 0.000],
    [-0.070, 0.000, 0.000, 0.000],
    [-0.060, 0.000, 0.000],
    [-0.060, 0.000],
    [-0.050]
]

sys_cor_dG_m = [
    [0.94, 0.96, 0.98, 0.75, 0.96, 0.77, 0.75, 0.66],
    [0.99, 0.97, 0.93, 0.88, 0.91, 0.91, 0.81],
    [0.99, 0.90, 0.92, 0.89, 0.89, 0.78],
    [0.84, 0.96, 0.85, 0.84, 0.73],
    [0.70, 0.97, 0.98, 0.88],
    [0.77, 0.75, 0.64],
    [0.99, 0.91],
    [0.91], 
]

# # Einheit 1 / ns
# dG_0 = [
#     "0.71(7)",
#     "0.66(7)",
#     "0.56(6)",
#     "0.57(6)",
#     "0.48(6)",
#     "0.54(7)",
#     "0.37(7)"
# ]
#
# # Korrelationsmatrizen
# stat_cor_dG_0 = [
#     [0.773, 0.171, 0.614, 0.675, 0.273, -0.182],
#     [0.371, 0.709, 0.600, 0.625, -0.031],
#     [-0.051, 0.713, 0.480, 0.480],
#     [0.229, 0.497, 0.110],
#     [0.536, 0.145],
#     [0.273],
# ]
#
# sys_cor_dG_0 = [
#     [-0.092, 0.008, 0.000, 0.000, 0.000, -0.005],
#     [-0.121, 0.011, 0.000, 0.000, -0.006],
#     [-0.133, 0.011, -0.001, 0.008],
#     [-0.128, 0.006, -0.013],
#     [-0.104, -0.016],
#     [-0.095],
# ]
#
# stat_sigma = [2.03, 2.19, 2.31, 3.23, 3.82,3.98, 5.04, 6.88, 10.63] # übernommene werte
# sys_sigma = [2.52, 2.42, 2.36, 2.33, 2.47, 2.23, 2.08, 2.16, 3.03]
#
# stat_cor_dG_0 = np.diag([6.84, 7.29, 7.90, 8.06, 8.87, 8.42, 8.63])
# sys_cor_dG_0 = np.diag([1.44, 1.13, 1.27, 1.22, 1.22, 1.31, 1.30])
#
# dG_0_nom = get_nom(dG_0)
# cov_inv_dG_0 = get_invcov(dG_0_nom, stat_cor_dG_0, sys_cor_dG_0)
