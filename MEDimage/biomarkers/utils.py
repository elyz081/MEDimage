#!/usr/bin/env python3
# -*- coding: utf-8 -*-


from typing import Tuple, Union

import numpy as np


def findIX(levels, fractVol, x) -> np.ndarray:
    """Computes intensity at volume fraction.

    Args:
        levels (ndarray): COMPLETE INTEGER grey-levels.
        fractVol (ndarray): Fractional volume.
        x (float): Fraction percentage, between 0 and 100.

    Returns:
        ndarray: Array of minimum discretised intensity present 
            in at most `x`% of the volume.
    
    """
    ind = np.where(fractVol <= x/100)[0][0]
    Ix = levels[ind]

    return Ix
    
def findVX(fractInt, fractVol, x) -> np.ndarray:
    """Computes volume at intensity fraction.

    Args:
        fractInt (ndarray): Intensity fraction.
        fractVol (ndarray): Fractional volume.
        x (float): Fraction percentage, between 0 and 100.

    Returns:
        ndarray: Array of largest volume fraction `fractVol` that has an 
            intensity fraction `fractInt` of at least `x`%.

    """
    ind = np.where(fractInt >= x/100)[0][0]
    Vx = fractVol[ind]

    return Vx

def getAreaDensApprox(a, b, c, n) -> float:
    """Computes area density - minimum volume enclosing ellipsoid
    
    Args:
        a (float): Major semi-axis length.
        b (float): Minor semi-axis length.
        c (float): Least semi-axis length.
        n (int): Number of iterations.

    Returns:
        float: Area density - minimum volume enclosing ellipsoid.

    """
    alpha = np.sqrt(1 - b**2/a**2)
    beta = np.sqrt(1 - c**2/a**2)
    AB = alpha * beta
    point = (alpha**2+beta**2) / (2*AB)
    Aell = 0

    for v in range(0, n+1):
        coef = [0]*v + [1]
        legen = np.polynomial.legendre.legval(x=point, c=coef)
        Aell = Aell + AB**v / (1-4*v**2) * legen

    Aell = Aell * 4 * np.pi * a * b

    return Aell

def getAxisLengths(XYZ) -> Tuple[float, float, float]:
    """Computes AxisLengths.
    
    Args:
        XYZ (ndarray): Array of three column vectors, defining the [X,Y,Z]
            positions of the points in the ROI (1's) of the mask volume. In mm.

    Returns:
        Tuple[float, float, float]: Array of three column vectors
            [Major axis lengths, Minor axis lengths, Least axis lengths].

    """
    XYZ = XYZ.copy()

    # Getting the geometric centre of mass
    com_geom = np.sum(XYZ, 0)/np.shape(XYZ)[0]  # [1 X 3] vector

    # Subtracting the centre of mass
    XYZ[:, 0] = XYZ[:, 0] - com_geom[0]
    XYZ[:, 1] = XYZ[:, 1] - com_geom[1]
    XYZ[:, 2] = XYZ[:, 2] - com_geom[2]

    # Getting the covariance matrix
    covMat = np.cov(XYZ, rowvar=False)

    # Getting the eigenvalues
    eigVal, _ = np.linalg.eig(covMat)
    eigVal = np.sort(eigVal)

    major = eigVal[2]
    minor = eigVal[1]
    least = eigVal[0]

    return major, minor, least

def getCOM(Xgl_int, Xgl_morph, XYZ_int, XYZ_morph) -> Union[float, np.ndarray]:
    """Calculates center of mass shift (in mm, since resolution is in mm).

    Note: 
        Row positions of "Xgl" and "XYZ" must correspond for each point.
    
    Args:
        Xgl_int (ndarray): Vector of intensity values in the volume to analyze 
            (only values in the intensity mask).
        Xgl_morph (ndarray): Vector of intensity values in the volume to analyze 
            (only values in the morphological mask).
        XYZ_int (ndarray): [nPoints X 3] matrix of three column vectors, defining the [X,Y,Z]
            positions of the points in the ROI (1's) of the mask volume (In mm).
            (Mesh-based volume calculated from the ROI intensity mesh)
        XYZ_morph (ndarray): [nPoints X 3] matrix of three column vectors, defining the [X,Y,Z]
            positions of the points in the ROI (1's) of the mask volume (In mm).
            (Mesh-based volume calculated from the ROI morphological mesh)

    Returns:
        Union[float, np.ndarray]: The ROI volume centre of mass.

    """

    # Getting the geometric centre of mass
    Nv = np.size(Xgl_morph)

    com_geom = np.sum(XYZ_morph, 0)/Nv  # [1 X 3] vector

    # Getting the density centre of mass
    XYZ_int[:, 0] = Xgl_int*XYZ_int[:, 0]
    XYZ_int[:, 1] = Xgl_int*XYZ_int[:, 1]
    XYZ_int[:, 2] = Xgl_int*XYZ_int[:, 2]
    com_gl = np.sum(XYZ_int, 0)/np.sum(Xgl_int, 0)  # [1 X 3] vector

    # Calculating the shift
    com = np.linalg.norm(com_geom - com_gl)

    return com