# MEDimage

## Table of Contents
  * [1. Introduction](#1-introduction)
  * [2. Installation](#2-installation)
  * [3. Generating the Documentation Locally](#3-generating-the-documentation-locally)
  * [4. IBSI Tests](#4-ibsi-tests)
    * [IBSI Chapter 1](#ibsi-chapter-1)
    * [IBSI Chapter 2](#ibsi-chapter-2)
  * [5. Project Files Organization](#4-project-files-organization)
  * [6. Authors](#5-authors)
  * [7. Statement](#6-statement)

## 1. Introduction
*MEDimage* is a Python package for processing and extracting features from medical images. It gives you the ability to process and filter images and compute all types of radiomic features. This package has been standarized with the [IBSI](https://theibsi.github.io/) norms.

## 2. Installation

### Cloning the repository
In your terminal, clone the repository
```
$ git clone https://github.com/MahdiAll99/MEDimage.git
```

Then access the package directory using:
```
$ cd MEDimage
```

### Making the environment
In order to run the package code, you need to create and activate the `medimage` environment
```
$ make create_environment
```
If the above command does not work try the following
```
$ make -f Makefile.mk create_environment
```
This command will install all the dependencies required. And now we activate the `medimage` environment with
```
$ conda activate medimage
```
For windows users, use 
```
$ activate medimage
```

Once the environment is activated, you can generate the documentation in the [doc section](#3-generating-the-documentation-locally) or start running the [IBSI-Tests](#4-ibsi-tests) without documentation (not recommended).

## 3. Generating the Documentation Locally
The package documentation can be generated locally using [pdoc3](https://pdoc.dev/docs/pdoc.html).

From your terminal, access the MEDimage package folder and follow these instructions

Activate the conda environment
```
$ conda activate medimage
```
Then we install the pdoc tool using
```
$ pip install pdoc3
```
And finally, generate the documentation with
```
$ pdoc3 --http localhost:8080 -c latex_math=True MEDimage
```

The documentation will be available on the *8080 localhost* via the link http://localhost:8080. The IBSI tests can now be run by following the [IBSI Tests](#4-ibsi-tests) section.

## 4. IBSI Tests
The image biomarker standardization initiative (IBSI) is an independent international collaboration which works towards standardizing the extraction of image biomarkers from acquired imaging. The IBSI therefore seeks to provide image biomarker nomenclature and definitions, benchmark data sets, and benchmark values to verify image processing and image biomarker calculations, as well as reporting guidelines, for high-throughput image analysis.

  - ### IBSI Chapter 1
      [The IBSI chapter 1](https://theibsi.github.io/ibsi1/) was initiated in September 2016, and it reached completion in March 2020 and is dedicated to the standardization of commonly used radiomic features. Two notebooks ([ibsi1p1.ipynb](https://github.com/MahdiAll99/MEDimage/blob/main/IBSI-TESTs/ibsi1p1.ipynb) and [ibsi1p2.ipynb](https://github.com/MahdiAll99/MEDimage/blob/main/IBSI-TESTs/ibsi1p2.ipynb)) have been made to test the MEDimage package implementations and validate image processing and image biomarker calculations.

  - ### IBSI Chapter 2
      [The IBSI chapter 2](https://theibsi.github.io/ibsi2/) was launched in June 2020 and still ongoing. It is dedicated to the standardization of commonly used imaging filters in radiomic studies. Two notebooks ([ibsi2p1.ipynb](https://github.com/MahdiAll99/MEDimage/blob/main/IBSI-TESTs/ibsi2p1.ipynb) and [ibsi2p2.ipynb](https://github.com/MahdiAll99/MEDimage/blob/main/IBSI-TESTs/ibsi2p2.ipynb)) have been made to test the MEDimage package implementations and validate image filtering and image biomarker calculations from filter response maps.

In order to run the notebooks we need to install *ipykernel* using the command 
```
$ conda install -c anaconda ipykernel
```
Now we add our `medimage` environment to the jupyter notebook kernels using
```
$ python -m ipykernel install --user --name=medimage
```
Finally, from the MEDimage folder we launch the jupyter notebook to navigate through the IBSI notebooks and have fun testing
```
$ jupyter notebook
```

Our team *UdeS* (a.k.a. Université de Sherbrooke) has already submitted the benchmarked values to the IBSI uploading website and can be found here: [IBSI upload page](https://ibsi.radiomics.hevs.ch/).

## 5. Project Files Organization
```
├── LICENSE
├── Makefile           <- Makefile with multiple commands for the environment setup.
├── README.md          <- The main README with Markdown language for this package.
├── IBSI-TESTs
│   ├── data           <- Data from IBSI.
│   ├── images         <- Figures used in the notebooks.
│   ├── settings       <- JSON files for configurations for each IBSI test.
│   ├── ibsi1p1.ipynb  <- IBSI chapter 1 phase 1 tutorial.
│   ├── ibsi1p2.ipynb  <- IBSI chapter 1 phase 2 tutorial.
│   ├── ibsi2p1.ipynb  <- IBSI chapter 2 phase 1 tutorial.
│   └── ibsi2p2.ipynb  <- IBSI chapter 2 phase 2 tutorial.
|
├── MEDimage           <- Source code for the MEDimage mother class and all the child classes
|   |                     and for image filtering as well.
│   ├── utils          <- Scripts for all the useful methods that will be called in other parts
|   |                     of the package.
│   ├── processing     <- Scripts for image processing (interpolation, re-segmentation...).
│   ├── biomarkers     <- Scripts to extract features and do all features-based computations.
│   ├── __init__.py    <- Makes MEDimage a Python module.
│   ├── Filter.py
│   ├── MEDimage.py
│   ├── MEDimageProcessing.py
│   └── MEDimageComputeRadiomics.py
|
├── environment.py     <- The dependencies file to create the `medimage` environment.
│
└── setup.py           <- Allows MEDimage package to be installed as a python package.
```

## 6. Authors
* [MEDOMICS](https://github.com/medomics/): MEDomics consortium

## 7. Statement

This package is part of https://github.com/medomics, a package providing research utility tools for developing precision medicine applications.

--> Copyright (C) 2020 MEDomics consortium

```
This package is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This package is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this package.  If not, see <http://www.gnu.org/licenses/>.
```
