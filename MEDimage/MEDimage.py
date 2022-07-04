import logging
import os
from json import dump
from pathlib import Path
from typing import Dict, List, Union

import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np
from numpyencoder import NumpyEncoder
from PIL import Image

from MEDimage.processing.compute_suv_map import compute_suv_map

from .utils.imref import imref3d
from .utils.json_utils import load_json


class MEDimage(object):
    """Organizes all scan data (patientID, imaging data, scan type...). 

    Args:
        MEDimg (MEDimage, optional): A MEDimage instance.

    Attributes:
        patientID (str): Patient ID.
        type (str): Scan type (MRscan, CTscan...).
        format (str): Scan file format. Either 'npy' or 'nifti'.
        dicomH (pydicom.dataset.FileDataset): DICOM header.
        scan (MEDimage.scan): Instance of MEDimage.scan inner class.

    """

    def __init__(self, MEDimg=None, logger=None) -> None:
        try:
            self.patientID = MEDimg.patientID
        except:
            self.patientID = ""
        try:
            self.type = MEDimg.type
        except:
            self.type = ""
        try:
            self.format = MEDimg.format
        except:
            self.format = ""
        try:
            self.dicomH = MEDimg.dicomH
        except:
            self.dicomH = []
        try:
            self.scan = MEDimg.scan
        except:
            self.scan = self.scan()
        try:
            self.params = MEDimg.params
        except:
            self.params = self.Params()
        try:
            self.radiomics = MEDimg.radiomics
        except:
            self.radiomics = self.Radiomics()

        self.skip = False

        if logger == None:
            self._logger = 'MEDimage.log'
        else:
            self._logger = logger

        logging.basicConfig(filename=self._logger, level=logging.DEBUG)

    def __init_process_params(self, im_params: Dict) -> None:
        """Initializes the processing params from a given Dict."""
        if self.type == 'CTscan' and 'imParamCT' in im_params:
            im_params = im_params['imParamCT']
        elif self.type == 'MRscan' and 'imParamMR' in im_params:
            im_params = im_params['imParamMR']
        elif self.type == 'PTscan' and 'imParamPET' in im_params:
            im_params = im_params['imParamPET']

        # 10 voxels in all three dimensions are added to the smallest
        # bounding box. This setting is used to speed up interpolation
        # processes (mostly) prior to the computation of radiomics
        # features. Optional argument in the function computeRadiomics.
        box_string = 'box10'

        # get default scan parameters from im_param_scan
        self.params.process.scale_non_text = im_params['interp']['scale_non_text']
        self.params.process.vol_interp  = im_params['interp']['vol_interp']
        self.params.process.roi_interp = im_params['interp']['roi_interp']
        self.params.process.gl_round = im_params['interp']['gl_round']
        self.params.process.roi_pv  = im_params['interp']['roi_pv']
        self.params.process.im_range = im_params['reSeg']['range'] if 'range' in im_params['reSeg'] else None
        self.params.process.outliers = im_params['reSeg']['outliers']
        self.params.process.ih = im_params['discretisation']['IH']
        self.params.process.ivh = im_params['discretisation']['IVH']
        self.params.process.scale_text = im_params['interp']['scale_text']
        self.params.process.algo = im_params['discretisation']['texture']['type']
        self.params.process.gray_levels = im_params['discretisation']['texture']['val']
        self.params.process.im_type = im_params['type']

        # Variable used to determine if there is 'arbitrary' (e.g., MRI)
        # or 'definite' (e.g., CT) intensities.
        self.params.process.intensity = im_params['intensity']

        if 'compute_diag_features' in im_params:
            compute_diag_features = im_params['compute_diag_features']
        else:
            compute_diag_features = False

        if compute_diag_features:  # If compute_diag_features is true.
            box_string = 'full'  # This is required for proper comparison.

        self.params.process.box_string = box_string
        self.params.process.n_scale = len(self.params.process.scale_text)
        self.params.process.n_algo = len(self.params.process.algo)
        self.params.process.n_gl = len(self.params.process.gray_levels[0])
        self.params.process.n_exp = self.params.process.n_scale * self.params.process.n_algo * self.params.process.n_gl

        # Setting up user_set_min_value
        if self.params.process.im_range is not None and type(self.params.process.im_range) is list and self.params.process.im_range:
            user_set_min_value = self.params.process.im_range[0]
            if user_set_min_value == -np.inf:
                # In case no re-seg im_range is defined for the FBS algorithm,
                # the minimum value of ROI will be used (not recommended).
                user_set_min_value = []
        else:
            # In case no re-seg im_range is defined for the FBS algorithm,
            # the minimum value of ROI will be used (not recommended).
            user_set_min_value = [] 
        self.params.process.user_set_min_value = user_set_min_value

        # box_string argument is optional. If not present, we use the full box.
        if self.params.process.box_string is None:
            self.params.process.box_string = 'full'
        
    def __init_filter_params(self, filter_params) -> None:
        """Initializes the filtering params from a given Dict."""
        if 'imParamFilter' in filter_params:
            print(filter_params)
            filter_params = filter_params['imParamFilter']
        # mean filter params
        self.params.filter.mean.init_from_json(filter_params['mean'])

        # log filter params
        self.params.filter.log.init_from_json(filter_params['log'])

        # laws filter params
        self.params.filter.laws.init_from_json(filter_params['laws'])

        # gabor filter params
        self.params.filter.gabor.init_from_json(filter_params['gabor'])

        # wavelet filter params
        self.params.filter.wavelet.init_from_json(filter_params['wavelet'])

    def init_params(self, im_param_scan):

        try:
            # get default scan parameters from im_param_scan
            self.__init_process_params(im_param_scan)
            self.__init_filter_params(im_param_scan['imParamFilter'])

            # compute suv map for PT scans
            if self.type == 'PTscan':
                _compute_suv_map = im_param_scan['computeSUVmap']
            else :
                _compute_suv_map = False
            
            if self.type == 'PTscan' and _compute_suv_map:
                try:
                    self.scan.volume.data = compute_suv_map(self.scan.volume.data, self.dicomH[0])
                except Exception as e :
                    message = f"\n ERROR COMPUTING SUV MAP - SOME FEATURES WILL BE INVALID: \n {e}"
                    logging.error(message)
                    print(message)
                    self.skip = True
            
            # initialize radiomics structure
            self.radiomics.image = {}
            self.radiomics.params = im_param_scan
            self.params.radiomics.scale_name = ''
            self.params.radiomics.ih_name = ''
            self.params.radiomics.ivh_name = ''
            
        except Exception as e:
            message = f"\n ERROR IN INITIALIZATION OF RADIOMICS FEATURE COMPUTATION\n {e}"
            logging.error(message)
            print(message)
            self.skip = True

    def init_ntf_calculation(self, vol_obj) -> None:
        """
        Initializes all the computation parameters for NON-TEXTURE FEATURES 
        as well as the results dict.
        """
        try:
            if sum(self.params.process.scale_non_text) == 0:  # In case the user chose to not interpolate
                self.params.process.scale_non_text = [
                                        vol_obj.spatialRef.PixelExtentInWorldX,
                                        vol_obj.spatialRef.PixelExtentInWorldY,
                                        vol_obj.spatialRef.PixelExtentInWorldZ]
            else:
                if len(self.params.process.scale_non_text) == 2:
                    # In case not interpolation is performed in
                    # the slice direction (e.g. 2D case)
                    self.params.process.scale_non_text = self.params.process.scale_non_text + \
                        [vol_obj.spatialRef.PixelExtentInWorldZ]

            # Scale name
            # Always isotropic resampling, so the first entry is ok.
            self.params.radiomics.scale_name = 'scale' + (str(self.params.process.scale_non_text[0])).replace('.', 'dot')

            # IH name
            ih_val_name = 'bin' + (str(self.params.process.ih['val'])).replace('.', 'dot')

            # The minimum value defines the computation.
            if self.params.process.ih['type'].find('FBS')>=0:
                if type(self.params.process.user_set_min_value) is list and self.params.process.user_set_min_value:
                    min_val_name = '_min' + \
                        ((str(self.params.process.user_set_min_value)).replace('.', 'dot')).replace('-', 'M')
                else:
                    # Otherwise, minimum value of ROI will be used (not recommended),
                    # so no need to report it.
                    min_val_name = ''
            else:
                min_val_name = ''
            self.params.radiomics.ih_name = self.params.radiomics.scale_name + \
                                            '_algo' + self.params.process.ih['type'] + \
                                            '_' + ih_val_name + min_val_name

            # IVH name
            if not self.params.process.ivh:  # CT case
                ivh_algo_name = 'algoNone'
                ivh_val_name = 'bin1'
                if self.params.process.im_range:  # The im_range defines the computation.
                    min_val_name = ((str(self.params.process.im_range[0])).replace(
                        '.', 'dot')).replace('-', 'M')
                    max_val_name = ((str(self.params.process.im_range[1])).replace(
                        '.', 'dot')).replace('-', 'M')
                    range_name = '_min' + min_val_name + '_max' + max_val_name
                else:
                    range_name = ''
            else:
                ivh_algo_name = 'algo' + self.params.process.ivh['type']
                ivh_val_name = 'bin' + (str(self.params.process.ivh['val'])).replace('.', 'dot')
                # The im_range defines the computation.
                if 'type' in self.params.process.ivh and self.params.process.ivh['type'].find('FBS') >=0:
                    if self.params.process.im_range:
                        min_val_name = ((str(self.params.process.im_range[0])).replace(
                            '.', 'dot')).replace('-', 'M')
                        max_val_name = ((str(self.params.process.im_range[1])).replace(
                            '.', 'dot')).replace('-', 'M')
                        if max_val_name == 'inf':
                            # In this case, the maximum value of the ROI is used,
                            # so no need to report it.
                            range_name = '_min' + min_val_name
                        elif min_val_name == '-inf':
                            # In this case, the minimum value of the ROI is used,
                            # so no need to report it.
                            range_name = '_max' + max_val_name
                        else:
                            range_name = '_min' + min_val_name + '_max' + max_val_name
                    else:  # min-max of ROI will be used, no need to report it.
                        range_name = ''
                else:  # min-max of ROI will be used, no need to report it.
                    range_name = ''
            self.params.radiomics.ivh_name = self.params.radiomics.scale_name + '_' + ivh_algo_name + '_' + ivh_val_name + range_name

            # Now initialize the attribute that will hold the computation results
            self.radiomics.image.update({ 
                            'morph_3D': {self.params.radiomics.scale_name: {}},
                            'locInt_3D': {self.params.radiomics.scale_name: {}},
                            'stats_3D': {self.params.radiomics.scale_name: {}},
                            'intHist_3D': {self.params.radiomics.ih_name: {}},
                            'intVolHist_3D': {self.params.radiomics.ivh_name: {}} 
                            })

        except Exception as e:
            message = f"\n PROBLEM WITH PRE-PROCESSING OF FEATURES IN init_NTF_Calculation(): \n {e}"
            logging.error(message)
            print(message)
            self.radiomics.image.update(
                    {('scale' + (str(self.params.process.scale_non_text[0])).replace('.', 'dot')): 'ERROR_PROCESSING'})

    def init_tf_Calculation(self, algo:int, gl:int, scale:int) -> None:
        """
        Initializes all the computation parameters for TEXTURE FEATURES 
        as well as the results dict.
        """
        self.params.radiomics.name_text_types = ['glcm_3D', 'glrlm_3D', 'glszm_3D', 'gldzm_3D', 'ngtdm_3D', 'ngldm_3D']

        n_text_types = len(self.params.radiomics.name_text_types)
        if not ('texture' in self.radiomics.image):
            self.radiomics.image.update({'texture': {}})
            for t in range(n_text_types):
                self.radiomics.image.update({self.params.radiomics.name_text_types[t]: {}})

        # scale name
        # Always isotropic resampling, so the first entry is ok.
        scale_name = 'scale' + (str(self.params.process.scale_text[scale][0])).replace('.', 'dot')
        if hasattr(self.params.radiomics, "scale_name"):
            setattr(self.params.radiomics, 'scale_name', scale_name)
        else:
            self.params.radiomics.scale_name = scale_name

        # Discretisation name
        gray_levels_name = (str(self.params.process.gray_levels[algo][gl])).replace('.', 'dot')

        if 'FBS' in self.params.process.algo[algo]:  # The minimum value defines the computation.
            if type(self.params.process.user_set_min_value) is list and self.params.process.user_set_min_value:
                min_val_name = '_min' + ((str(self.params.process.user_set_min_value)).replace('.', 'dot')).replace('-', 'M')
            else:
                # Otherwise, minimum value of ROI will be used (not recommended),
                # so no need to report it.
                min_val_name = ''
        else:
            min_val_name = ''

        if 'equal'in self.params.process.algo[algo]:
            # The number of gray-levels used for equalization is currently
            # hard-coded to 64 in equalization.m
            discretisation_name = 'algo' + self.params.process.algo[algo] + '256_bin' + gray_levels_name + min_val_name
        else:
            discretisation_name = 'algo' + self.params.process.algo[algo] + '_bin' + gray_levels_name + min_val_name

        # Processing full name
        processing_name = scale_name + '_' + discretisation_name
        if hasattr(self.params.radiomics, "processing_name"):
            setattr(self.params.radiomics, 'processing_name', processing_name)
        else:
            self.params.radiomics.processing_name = processing_name

    def init_from_nifti(self, nifti_image_path) -> None:
        """Initializes the MEDimage class using a NIfTI file.

        Args:
            nifti_image_path (Path): NIfTI file path.

        Returns:
            None.
        
        """
        self.patientID = os.path.basename(nifti_image_path).split("_")[0]
        self.type = os.path.basename(nifti_image_path).split(".")[-3]
        self.format = "nifti"
        self.scan.set_orientation(orientation="Axial")
        self.scan.set_patientPosition(patientPosition="HFS")
        self.scan.ROI.get_ROI_from_path(ROI_path=os.path.dirname(nifti_image_path), 
                                        ID=Path(nifti_image_path).name.split("(")[0])
        self.scan.volume.data = nib.load(nifti_image_path).get_fdata()
        # RAS to LPS
        self.scan.volume.convert_to_LPS()
        self.scan.volume.scan_rot = None
    
    def update_radiomics(
                        self, int_vol_hist_features: Dict = None, 
                        morph_features: Dict = None, loc_int_features: Dict = None, 
                        stats_features: Dict = None, int_hist_features: Dict = None,
                        glcm_features: Dict = None, glcm_merge_method: str = None, 
                        glrlm_features: Dict = None, glrlm_method: str = None, 
                        glszm_features: Dict = None, gldzm_features: Dict = None, 
                        ngtdm_features: Dict = None, ngldm_features: Dict = None) -> None:
        """
        Updates the results attribute with the extracted features
        """
        # check glcm merge method
        if glcm_merge_method:
            if glcm_merge_method == 'average':
                glcm_merge_method = '_avg'
            elif glcm_merge_method == 'vol_merge':
                glcm_merge_method = '_comb'
            else:
                error_msg = f"{glcm_merge_method} Method not supported in glcm computation, \
                    only 'average' or 'vol_merge' are supported. \
                    Radiomics will be saved without any specific merge method."
                logging.warning(error_msg)
                print(error_msg)

        # check glrlm merge method
        if glrlm_method:
            if glrlm_method == 'average':
                glrlm_method = '_avg'
            elif glrlm_method == 'vol_merge':
                glrlm_method = '_comb'
            else:
                error_msg = f"{glcm_merge_method} Method not supported in glrlm computation, \
                    only 'average' or 'vol_merge' are supported. \
                    Radiomics will be saved without any specific merge method"
                logging.warning(error_msg)
                print(error_msg)

        # Non-texture Features
        if int_vol_hist_features:
            self.radiomics.image['intVolHist_3D'][self.params.radiomics.ivh_name] = int_vol_hist_features
        if morph_features:
            self.radiomics.image['morph_3D'][self.params.radiomics.scale_name] = morph_features
        if loc_int_features:
            self.radiomics.image['locInt_3D'][self.params.radiomics.scale_name] = loc_int_features
        if stats_features:
            self.radiomics.image['stats_3D'][self.params.radiomics.scale_name] = stats_features
        if int_hist_features:
            self.radiomics.image['intHist_3D'][self.params.radiomics.ih_name] = int_hist_features
        
        # Texture Features
        if glcm_features:
            self.radiomics.image['glcm_3D' + glcm_merge_method][self.params.radiomics.processing_name] = glcm_features
        if glrlm_features:
            self.radiomics.image['glrlm_3D' + glrlm_method][self.params.radiomics.processing_name] = glrlm_features
        if glszm_features:
            self.radiomics.image['glszm_3D'][self.params.radiomics.processing_name] = glszm_features
        if gldzm_features:
            self.radiomics.image['gldzm_3D'][self.params.radiomics.processing_name] = gldzm_features
        if ngtdm_features:
            self.radiomics.image['ngtdm_3D'][self.params.radiomics.processing_name] = ngtdm_features
        if ngldm_features:
            self.radiomics.image['ngldm_3D'][self.params.radiomics.processing_name] = ngldm_features

    def save_radiomics(
                    self, scan_file_name: List, 
                    path_save: Path, roi_type: str, 
                    roi_type_label: str, patient_num: int) -> None:
        """
        Saves extracted radiomics features in a JSON file.
        """
        path_save = Path(path_save)
        params = {}
        params['roi_type'] = roi_type
        params['patientID'] = self.patientID
        params['vox_dim'] = list([
                                self.scan.volume.spatialRef.PixelExtentInWorldX, 
                                self.scan.volume.spatialRef.PixelExtentInWorldY,
                                self.scan.volume.spatialRef.PixelExtentInWorldZ
                                ])
        self.radiomics.update_params(params)
        indDot = scan_file_name[patient_num].find('.')
        ext = scan_file_name[patient_num].find('.npy')
        nameSave = scan_file_name[patient_num][:indDot] + \
            '(' + roi_type_label + ')' + scan_file_name[patient_num][indDot:ext]

        # IMPORTANT: HERE, WE COULD ADD SOME CODE TO APPEND A NEW "radiomics"
        # STRUCTURE TO AN EXISTING ONE WITH THE SAME NAME IN "pathSave"
        with open(path_save / f"{nameSave}.json", "w") as fp:   
            dump(self.radiomics.to_json(), fp, indent=4, cls=NumpyEncoder)

    class Params:
        """Organizes all processing, filtering and features extraction"""

        def __init__(self) -> None:
            """Organizes all processing, filtering and features extraction
            """
            self.process = self.Process()
            self.filter = self.Filter()
            self.radiomics = self.Radiomics()

        class Process:
            def __init__(self, **kwargs) -> None:
                """
                Organizes all processing, filtering and features extraction
                """
                self.algo = kwargs['algo'] if 'algo' in kwargs else None
                self.box_string = kwargs['box_string'] if 'box_string' in kwargs else None
                self.gl_round = kwargs['gl_round'] if 'gl_round' in kwargs else None
                self.gray_levels = kwargs['gray_levels'] if 'gray_levels' in kwargs else None
                self.ih = kwargs['ih'] if 'ih' in kwargs else None
                self.im_range = kwargs['im_range'] if 'im_range' in kwargs else None
                self.im_type = kwargs['im_type'] if 'im_type' in kwargs else None
                self.intensity = kwargs['intensity'] if 'intensity' in kwargs else None
                self.ivh = kwargs['ivh'] if 'ivh' in kwargs else None
                self.n_algo = kwargs['n_algo'] if 'n_algo' in kwargs else None
                self.n_exp = kwargs['n_exp'] if 'n_exp' in kwargs else None
                self.n_gl = kwargs['n_gl'] if 'n_gl' in kwargs else None
                self.n_scale = kwargs['n_scale'] if 'n_scale' in kwargs else None
                self.outliers = kwargs['outliers'] if 'outliers' in kwargs else None
                self.scale_non_text = kwargs['scale_non_text'] if 'scale_non_text' in kwargs else None
                self.scale_text = kwargs['scale_text'] if 'scale_text' in kwargs else None
                self.roi_interp = kwargs['roi_interp'] if 'roi_interp' in kwargs else None
                self.roi_pv = kwargs['roi_pv'] if 'roi_pv' in kwargs else None
                self.user_set_min_value = kwargs['user_set_min_value'] if 'user_set_min_value' in kwargs else None
                self.vol_interp = kwargs['vol_interp'] if 'vol_interp' in kwargs else None

            def init_from_json(self, path_to_json: Union[Path, str]) -> None:
                """
                Updates params attributes from json file
                """
                __params = load_json(path_to_json)

                self.algo = __params['algo'] if 'algo' in __params else self.algo
                self.box_string = __params['box_string'] if 'box_string' in __params else self.box_string
                self.gl_round = __params['gl_round'] if 'gl_round' in __params else self.gl_round
                self.gray_levels = __params['gray_levels'] if 'gray_levels' in __params else self.gray_levels
                self.ih = __params['ih'] if 'ih' in __params else self.ih
                self.im_range = __params['im_range'] if 'im_range' in __params else self.im_range
                self.im_type = __params['im_type'] if 'im_type' in __params else self.im_type
                self.intensity = __params['intensity'] if 'intensity' in __params else self.intensity
                self.ivh = __params['ivh'] if 'ivh' in __params else self.ivh
                self.n_algo = __params['n_algo'] if 'n_algo' in __params else self.n_algo
                self.n_exp = __params['n_exp'] if 'n_exp' in __params else self.n_exp
                self.n_gl = __params['n_gl'] if 'n_gl' in __params else self.n_gl
                self.n_scale = __params['n_scale'] if 'n_scale' in __params else self.n_scale
                self.outliers = __params['outliers'] if 'outliers' in __params else self.outliers
                self.scale_non_text = __params['scale_non_text'] if 'scale_non_text' in __params else self.scale_non_text
                self.scale_text = __params['scale_text'] if 'scale_text' in __params else self.scale_text
                self.roi_interp = __params['roi_interp'] if 'roi_interp' in __params else self.roi_interp
                self.roi_pv = __params['roi_pv'] if 'roi_pv' in __params else self.roi_pv
                self.user_set_min_value = __params['user_set_min_value'] if 'user_set_min_value' in __params else self.user_set_min_value
                self.vol_interp = __params['vol_interp'] if 'vol_interp' in __params else self.vol_interp


        class Filter:
            def __init__(self) -> None:
                self.mean = self.Mean()
                self.log = self.Log()
                self.gabor = self.Gabor()
                self.laws = self.Laws()
                self.wavelet = self.Wavelet()


            class Mean:
                def __init__(self, 
                            ndims: int = 0, name_save: str = '', 
                            padding: str = '', size: int = 0) -> None:
                    """
                    Updates params attributes from json file
                    """
                    self.name_save = name_save
                    self.ndims = ndims
                    self.padding = padding
                    self.size = size

                def init_from_json(self, params: Dict) -> None:
                    """Updates mean filter params from a given dict"""
                    self.name_save = params['name_save']
                    self.ndims = params['ndims']
                    self.padding = params['padding']
                    self.size = params['size']


            class Log:
                def __init__(self, 
                            ndims: int = 0, sigma: float = 0.0, 
                            padding: str = '', orthogonal_rot: bool = False, 
                            name_save: str = '') -> None:
                    """
                    Updates params attributes from json file
                    """
                    self.name_save = name_save
                    self.ndims = ndims
                    self.orthogonal_rot = orthogonal_rot
                    self.padding = padding
                    self.sigma = sigma

                def init_from_json(self, params: Dict) -> None:
                    """Updates mean filter params from a given dict"""
                    self.name_save = params['name_save']
                    self.ndims = params['ndims']
                    self.orthogonal_rot = params['orthogonal_rot']
                    self.padding = params['padding']
                    self.sigma = params['sigma']


            class Gabor:
                def __init__(self, 
                            sigma: float = 0.0, _lambda: float = 0.0,  
                            gamma: float = 0.0, theta: str = '', rot_invariance: bool = False,
                            orthogonal_rot: bool= False, name_save: str = '',
                            padding: str = '') -> None:
                    """
                    Updates params attributes from json file
                    """
                    self._lambda = _lambda
                    self.gamma = gamma
                    self.name_save = name_save
                    self.orthogonal_rot = orthogonal_rot
                    self.padding = padding
                    self.rot_invariance = rot_invariance
                    self.sigma = sigma
                    self.theta = theta

                def init_from_json(self, params: Dict) -> None:
                    """Updates mean filter params from a given dict"""
                    self._lambda = params['_lambda']
                    self.gamma = params['gamma']
                    self.name_save = params['name_save']
                    self.orthogonal_rot = params['orthogonal_rot']
                    self.padding = params['padding']
                    self.rot_invariance = params['rot_invariance']
                    self.sigma = params['sigma']
                    self.theta = params['theta']


            class Laws:
                def __init__(self, 
                            config: List = [], energy_distance: int = 0, energy_image: bool = False, 
                            rot_invariance: bool = False, orthogonal_rot: bool = False, name_save: str = '',
                            padding: str = '') -> None:
                    """
                    Updates params attributes from json file
                    """
                    self.config = config
                    self.energy_distance = energy_distance
                    self.energy_image = energy_image
                    self.name_save = name_save
                    self.orthogonal_rot = orthogonal_rot
                    self.padding = padding
                    self.rot_invariance = rot_invariance

                def init_from_json(self, params: Dict) -> None:
                    """Updates mean filter params from a given dict"""
                    self.config = params['config']
                    self.energy_distance = params['energy_distance']
                    self.energy_image = params['energy_image']
                    self.name_save = params['name_save']
                    self.orthogonal_rot = params['orthogonal_rot']
                    self.padding = params['padding']
                    self.rot_invariance = params['rot_invariance']


            class Wavelet:
                def __init__(self, 
                            ndims: int = 0, name_save: str = '', 
                            basis_function: str = '', subband: str = '', level: int = 0, 
                            rot_invariance: bool = False, padding: str = '') -> None:
                    """
                    Updates params attributes from json file
                    """
                    self.basis_function = basis_function
                    self.level = level
                    self.ndims = ndims
                    self.name_save = name_save
                    self.padding = padding
                    self.rot_invariance = rot_invariance
                    self.subband = subband

                def init_from_json(self, params: Dict) -> None:
                    """Updates mean filter params from a given dict"""
                    self.basis_function = params['basis_function']
                    self.level = params['level']
                    self.ndims = params['ndims']
                    self.name_save = params['name_save']
                    self.padding = params['padding']
                    self.rot_invariance = params['rot_invariance']
                    self.subband = params['subband']


        class Radiomics:
            def __init__(self, **kwargs) -> None:
                """
                Features extraction parameters
                """
                self.ih_name = kwargs['ih_name'] if 'ih_name' in kwargs else None
                self.ivh_name = kwargs['ivh_name'] if 'ivh_name' in kwargs else None
                self.glcm = self.GLCM()
                self.glrlm = self.GLRLM()
                self.gldzm = self.GLDZM()
                self.ngtdm = self.NGTDM()
                self.ngldm = self.NGLDM()
                self.name_text_types = kwargs['name_text_types'] if 'name_text_types' in kwargs else None
                self.processing_name = kwargs['processing_name'] if 'processing_name' in kwargs else None
                self.scale_name = kwargs['scale_name'] if 'scale_name' in kwargs else None


            class GLCM:
                def __init__(self, 
                            symmetry: str = None,
                            distance_norm: Dict = None,
                            dist_correction: bool = False) -> None:
                    self.symmetry = symmetry
                    self.distance_norm = distance_norm
                    self.dist_correction = dist_correction


            class GLRLM:
                def __init__(self, 
                            dist_correction: bool = False) -> None:
                    self.dist_correction = dist_correction


            class GLDZM:
                def __init__(self, 
                            symmetry: str = None,
                            distance_norm: Dict = None,
                            dist_correction: bool = False) -> None:
                    self.symmetry = symmetry
                    self.distance_norm = distance_norm
                    self.dist_correction = dist_correction


            class NGTDM:
                def __init__(self, 
                            distance_norm: Dict = None) -> None:
                    self.distance_norm = distance_norm


            class NGLDM:
                def __init__(self, 
                            distance_norm: Dict = None) -> None:
                    self.distance_norm = distance_norm


    class Radiomics:
        """Organized all extracted features.

        Attributes:
            image (Dict): Dict contating the extracted features.
            params (Dict): Dict of the parameters used in features extraction (roi type, voxels diemension...)

        """
        def __init__(self, image: Dict = None, params: Dict = None) -> None:
            self.image = image if image else {}
            self.params = params if params else {}

        def update_params(self, params: Dict) -> None:
            """Updates the radiomics params attribute"""
            self.params['roi_type'] = params['roi_type']
            self.params['patientID'] = params['patientID']
            self.params['vox_dim'] = params['vox_dim']

        def to_json(self) -> Dict:
            """Organized the radiomics class attributes in a Dict"""
            radiomics = {
                'image': self.image,
                'params': self.params
            }
            return radiomics


    class scan:
        """Organizes all imaging data (volume and ROI). 

        Args:
            orientation (str, optional): Imaging data orientation (axial, sagittal or coronal).
            patientPosition (str, optional): Patient position specifies the position of the 
                patient relative to the imaging equipment space (HFS, HFP...).

        Attributes:
            volume (object): Instance of MEDimage.scan.volume inner class.
            ROI (object): Instance of MEDimage.scan.ROI inner class.
            orientation (str): Imaging data orientation (axial, sagittal or coronal).
            patientPosition (str): Patient position specifies the position of the 
                patient relative to the imaging equipment space (HFS, HFP...).

        """
        def __init__(self, orientation=None, patientPosition=None):
            self.volume = self.volume() 
            self.volume_process = self.volume_process()
            self.ROI = self.ROI()
            self.orientation = orientation
            self.patientPosition = patientPosition

        def set_patientPosition(self, patientPosition):
            self.patientPosition = patientPosition

        def set_orientation(self, orientation):
            self.orientation = orientation
        
        def set_volume(self, volume):
            self.volume = volume
        
        def set_ROI(self, *args):
            self.ROI = self.ROI(args)

        def get_ROI_from_indexes(self, key):
            """
            Extract ROI data using the saved indexes (Indexes of 1's).

            Args:
                ket (int): ROI index (A volume can have multiple ROIs).

            Returns:
                ndarray: n-dimensional array of ROI data.
            
            """
            roi_volume = np.zeros_like(self.volume.data).flatten()
            roi_volume[self.ROI.get_indexes(key)] = 1
            return roi_volume.reshape(self.volume.data.shape)

        def get_indexes_by_ROIname(self, ROIname : str):
            """
            Extract ROI data using ROI name..

            Args:
                ROIname (str): String of the ROI name (A volume can have multiple ROIs).

            Returns:
                ndarray: n-dimensional array of ROI data.
            
            """
            ROIname_key = list(self.ROI.roi_names.values()).index(ROIname)
            roi_volume = np.zeros_like(self.volume.data).flatten()
            roi_volume[self.ROI.get_indexes(ROIname_key)] = 1
            return roi_volume.reshape(self.volume.data.shape)

        def display(self, _slice: int = None) -> None:
            """Displays slices from imaging data with the ROI contour in XY-Plane.

            Args:
                _slice (int, optional): Index of the slice you want to plot.

            Returns:
                None.
            
            """
            # extract slices containing ROI
            size_m = self.volume.data.shape
            i = np.arange(0, size_m[0])
            j = np.arange(0, size_m[1])
            k = np.arange(0, size_m[2])
            ind_mask = np.nonzero(self.get_ROI_from_indexes(0))
            J, I, K = np.meshgrid(j, i, k, indexing='ij')
            I = I[ind_mask]
            J = J[ind_mask]
            K = K[ind_mask]
            slices = np.unique(K)

            vol_data = self.volume.data.swapaxes(0, 1)[:, :, slices]
            roi_data = self.get_ROI_from_indexes(0).swapaxes(0, 1)[:, :, slices]        
            
            rows = int(np.round(np.sqrt(len(slices))))
            columns = int(np.ceil(len(slices) / rows))
            
            plt.set_cmap(plt.gray())
            
            # plot only one slice
            if _slice:
                fig, ax =  plt.subplots(1, 1, figsize=(10, 5))
                ax.axis('off')
                ax.set_title(_slice)
                ax.imshow(vol_data[:, :, _slice])
                im = Image.fromarray((roi_data[:, :, _slice]))
                ax.contour(im, colors='red', linewidths=0.4, alpha=0.45)
                lps_ax = fig.add_subplot(1, columns, 1)
            
            # plot multiple slices containing an ROI.
            else:
                fig, axs =  plt.subplots(rows, columns+1, figsize=(20, 10))
                s = 0
                for i in range(0,rows):
                    for j in range(0,columns):
                        axs[i,j].axis('off')
                        if s < len(slices):
                            axs[i,j].set_title(str(s))
                            axs[i,j].imshow(vol_data[:, :, s])
                            im = Image.fromarray((roi_data[:, :, s]))
                            axs[i,j].contour(im, colors='red', linewidths=0.4, alpha=0.45)
                        s += 1
                    axs[i,columns].axis('off')
                lps_ax = fig.add_subplot(1, columns+1, axs.shape[1])

            fig.suptitle('XY-Plane')
            fig.tight_layout()
            
            # add the coordinates system
            lps_ax.axis([-1.5, 1.5, -1.5, 1.5])
            lps_ax.set_title("Coordinates system")
            
            lps_ax.quiver([-0.5], [0], [1.5], [0], scale_units='xy', angles='xy', scale=1.0, color='green')
            lps_ax.quiver([-0.5], [0], [0], [-1.5], scale_units='xy', angles='xy', scale=3, color='blue')
            lps_ax.quiver([-0.5], [0], [1.5], [1.5], scale_units='xy', angles='xy', scale=3, color='red')
            lps_ax.text(1.0, 0, "L")
            lps_ax.text(-0.3, -0.5, "P")
            lps_ax.text(0.3, 0.4, "S")

            lps_ax.set_xticks([])
            lps_ax.set_yticks([])

            plt.show()

        def display_process(self, _slice: int = None) -> None:
            """Displays slices from imaging data with the ROI contour in XY-Plane.

            Args:
                _slice (int, optional): Index of the slice you want to plot.

            Returns:
                None.
            
            """
            # extract slices containing ROI
            size_m = self.volume_process.data.shape
            i = np.arange(0, size_m[0])
            j = np.arange(0, size_m[1])
            k = np.arange(0, size_m[2])
            ind_mask = np.nonzero(self.get_ROI_from_indexes(0))
            J, I, K = np.meshgrid(j, i, k, indexing='ij')
            I = I[ind_mask]
            J = J[ind_mask]
            K = K[ind_mask]
            slices = np.unique(K)

            vol_data = self.volume_process.data.swapaxes(0, 1)[:, :, slices]
            roi_data = self.get_ROI_from_indexes(0).swapaxes(0, 1)[:, :, slices]        
            
            rows = int(np.round(np.sqrt(len(slices))))
            columns = int(np.ceil(len(slices) / rows))
            
            plt.set_cmap(plt.gray())
            
            # plot only one slice
            if _slice:
                fig, ax =  plt.subplots(1, 1, figsize=(10, 5))
                ax.axis('off')
                ax.set_title(_slice)
                ax.imshow(vol_data[:, :, _slice])
                im = Image.fromarray((roi_data[:, :, _slice]))
                ax.contour(im, colors='red', linewidths=0.4, alpha=0.45)
                lps_ax = fig.add_subplot(1, columns, 1)
            
            # plot multiple slices containing an ROI.
            else:
                fig, axs =  plt.subplots(rows, columns+1, figsize=(20, 10))
                s = 0
                for i in range(0,rows):
                    for j in range(0,columns):
                        axs[i,j].axis('off')
                        if s < len(slices):
                            axs[i,j].set_title(str(s))
                            axs[i,j].imshow(vol_data[:, :, s])
                            im = Image.fromarray((roi_data[:, :, s]))
                            axs[i,j].contour(im, colors='red', linewidths=0.4, alpha=0.45)
                        s += 1
                    axs[i,columns].axis('off')
                lps_ax = fig.add_subplot(1, columns+1, axs.shape[1])

            fig.suptitle('XY-Plane')
            fig.tight_layout()
            
            # add the coordinates system
            lps_ax.axis([-1.5, 1.5, -1.5, 1.5])
            lps_ax.set_title("Coordinates system")
            
            lps_ax.quiver([-0.5], [0], [1.5], [0], scale_units='xy', angles='xy', scale=1.0, color='green')
            lps_ax.quiver([-0.5], [0], [0], [-1.5], scale_units='xy', angles='xy', scale=3, color='blue')
            lps_ax.quiver([-0.5], [0], [1.5], [1.5], scale_units='xy', angles='xy', scale=3, color='red')
            lps_ax.text(1.0, 0, "L")
            lps_ax.text(-0.3, -0.5, "P")
            lps_ax.text(0.3, 0.4, "S")

            lps_ax.set_xticks([])
            lps_ax.set_yticks([])

            plt.show()


        class volume:
            """Organizes all volume data and information. 

            Args:
                spatialRef (imref3d, optional): Imaging data orientation (axial, sagittal or coronal).
                scan_rot (ndarray, optional): Array of the rotation applied to the XYZ points of the ROI.
                data (ndarray, optional): n-dimensional of the imaging data.
                filtered_data (Dict[ndarray]): Dict of n-dimensional arrays of the filtered 
                        imaging data.

            Attributes:
                spatialRef (imref3d): Imaging data orientation (axial, sagittal or coronal).
                scan_rot (ndarray): Array of the rotation applied to the XYZ points of the ROI.
                data (ndarray): n-dimensional of the imaging data.
                filtered_data (Dict[ndarray]): Dict of n-dimensional arrays of the filtered 
                    imaging data.

            """
            def __init__(self, spatialRef=None, scan_rot=None, data=None):
                """Organizes all volume data and information. 

                Args:
                    spatialRef (imref3d, optional): Imaging data orientation (axial, sagittal or coronal).
                    scan_rot (ndarray, optional): Array of the rotation applied to the XYZ points of the ROI.
                    data (ndarray, optional): n-dimensional of the imaging data.
                    filtered_data (Dict[ndarray]): Dict of n-dimensional arrays of the filtered 
                        imaging data.

                """
                self.spatialRef = spatialRef
                self.scan_rot = scan_rot
                self.data = data

            def update_spatialRef(self, spatialRef_value):
                self.spatialRef = spatialRef_value
            
            def update_scan_rot(self, scan_rot_value):
                self.scan_rot = scan_rot_value
            
            def update_transScanToModel(self, transScanToModel_value):
                self.transScanToModel = transScanToModel_value
            
            def update_data(self, data_value):
                self.data = data_value

            def convert_to_LPS(self):
                """Convert Imaging data to LPS (Left-Posterior-Superior) coordinates system.
                <https://www.slicer.org/wiki/Coordinate_systems>.

                Args:
                    ket (int): ROI index (A volume can have multiple ROIs).

                Returns:
                    None.

                """
                # flip x
                self.data = np.flip(self.data, 0)
                # flip y
                self.data = np.flip(self.data, 1)
            
            def spatialRef_from_NIFTI(self, nifti_image_path):
                """Computes the imref3d spatialRef using a NIFTI file and
                updates the spatialRef attribute.

                Args:
                    nifti_image_path (str): String of the NIFTI file path.

                Returns:
                    None.
                
                """
                # Loading the nifti file :
                nifti = nib.load(nifti_image_path)
                nifti_data = self.data

                # spatialRef Creation
                pixelX = nifti.affine[0, 0]
                pixelY = nifti.affine[1, 1]
                sliceS = nifti.affine[2, 2]
                min_grid = nifti.affine[:3, 3]
                min_Xgrid = min_grid[0]
                min_Ygrid = min_grid[1]
                min_Zgrid = min_grid[2]
                size_image = np.shape(nifti_data)
                spatialRef = imref3d(size_image, abs(pixelX), abs(pixelY), abs(sliceS))
                spatialRef.XWorldLimits = (np.array(spatialRef.XWorldLimits) -
                                        (spatialRef.XWorldLimits[0] -
                                            (min_Xgrid-pixelX/2))
                                        ).tolist()
                spatialRef.YWorldLimits = (np.array(spatialRef.YWorldLimits) -
                                        (spatialRef.YWorldLimits[0] -
                                            (min_Ygrid-pixelY/2))
                                        ).tolist()
                spatialRef.ZWorldLimits = (np.array(spatialRef.ZWorldLimits) -
                                        (spatialRef.ZWorldLimits[0] -
                                            (min_Zgrid-sliceS/2))
                                        ).tolist()

                # Converting the results into lists
                spatialRef.ImageSize = spatialRef.ImageSize.tolist()
                spatialRef.XIntrinsicLimits = spatialRef.XIntrinsicLimits.tolist()
                spatialRef.YIntrinsicLimits = spatialRef.YIntrinsicLimits.tolist()
                spatialRef.ZIntrinsicLimits = spatialRef.ZIntrinsicLimits.tolist()

                # update spatialRef
                self.update_spatialRef(spatialRef)

            def convert_spatialRef(self):
                """converts the MEDimage spatialRef from RAS to LPS coordinates system.
                <https://www.slicer.org/wiki/Coordinate_systems>.

                Args:
                    None.

                Returns:
                    None.

                """
                # swap x and y data
                temp = self.spatialRef.ImageExtentInWorldX
                self.spatialRef.ImageExtentInWorldX = self.spatialRef.ImageExtentInWorldY
                self.spatialRef.ImageExtentInWorldY = temp

                temp = self.spatialRef.PixelExtentInWorldX
                self.spatialRef.PixelExtentInWorldX = self.spatialRef.PixelExtentInWorldY
                self.spatialRef.PixelExtentInWorldY = temp

                temp = self.spatialRef.XIntrinsicLimits
                self.spatialRef.XIntrinsicLimits = self.spatialRef.YIntrinsicLimits
                self.spatialRef.YIntrinsicLimits = temp

                temp = self.spatialRef.XWorldLimits
                self.spatialRef.XWorldLimits = self.spatialRef.YWorldLimits
                self.spatialRef.YWorldLimits = temp
                del temp

        class volume_process:
            """Organizes all volume data and information. 

            Args:
                spatialRef (imref3d, optional): Imaging data orientation (axial, sagittal or coronal).
                scan_rot (ndarray, optional): Array of the rotation applied to the XYZ points of the ROI.
                data (ndarray, optional): n-dimensional of the imaging data.

            Attributes:
                spatialRef (imref3d): Imaging data orientation (axial, sagittal or coronal).
                scan_rot (ndarray): Array of the rotation applied to the XYZ points of the ROI.
                data (ndarray): n-dimensional of the imaging data.

            """
            def __init__(self, spatialRef: imref3d = None, 
                        scan_rot: List = None, data: np.ndarray = None,
                        user_string: str = "") -> None:
                """Organizes all volume data and information. 

                Args:
                    spatialRef (imref3d, optional): Imaging data orientation (axial, sagittal or coronal).
                    scan_rot (ndarray, optional): Array of the rotation applied to the XYZ points of the ROI.
                    data (ndarray, optional): n-dimensional of the imaging data.
                    filtered_data (Dict[ndarray]): Dict of n-dimensional arrays of the filtered 
                        imaging data.

                """
                self.data = data
                self.scan_rot = scan_rot
                self.spatialRef = spatialRef
                self.user_string = user_string
            
            def update_processed_data(self, data: np.ndarray, user_string: str = ""):
                if user_string:
                    self.user_string = user_string
                self.data = data

            def save(self, name_save: str, path: Union[Path, str]):
                path = Path(path)

                if not name_save:
                    name_save = self.user_string

                if not name_save.endswith('.npy'):
                    name_save += '.npy'

                with open(path / name_save, 'wb') as f:
                    np.save(f, self.data)

            def load(self, file_name, path, update=True):
                path = Path(path)

                if not file_name.endswith('.npy'):
                    file_name += '.npy'

                with open(path / file_name, 'rb') as f:
                    if update:
                        self.update_processed_data(np.load(f, allow_pickle=True))
                    else:
                        return np.load(f, allow_pickle=True)


        class ROI:
            """Organizes all ROI data and information. 

            Args:
                indexes (Dict, optional): Dict of the ROI indexes for each ROI name.
                roi_names (Dict, optional): Dict of the ROI names.

            Attributes:
                indexes (Dict): Dict of the ROI indexes for each ROI name.
                roi_names (Dict): Dict of the ROI names.
                nameSet (Dict): Dict of the User-defined name for Structure Set for each ROI name.
                nameSetInfo (Dict): Dict of the names of the structure sets that define the areas of 
                    significance. Either 'StructureSetName', 'StructureSetDescription', 'SeriesDescription' 
                    or 'SeriesInstanceUID'.

            """
            def __init__(self, indexes=None, roi_names=None) -> None:
                self.indexes = indexes if indexes else {}
                self.roi_names = roi_names if roi_names else {}
                self.nameSet = roi_names if roi_names else {}
                self.nameSetInfo = roi_names if roi_names else {}

            def get_indexes(self, key):
                if not self.indexes or key is None:
                    return {}
                else:
                    return self.indexes[str(key)]

            def get_ROIname(self, key):
                if not self.roi_names or key is None:
                    return {}
                else:
                    return self.roi_names[str(key)]

            def get_nameSet(self, key):
                if not self.nameSet or key is None:
                    return {}
                else:
                    return self.nameSet[str(key)]

            def get_nameSetInfo(self, key):
                if not self.nameSetInfo or key is None:
                    return {}
                else:
                    return self.nameSetInfo[str(key)]

            def update_indexes(self, key, indexes):
                try: 
                    self.indexes[str(key)] = indexes
                except:
                    Warning.warn("Wrong key given in update_indexes()")

            def update_ROIname(self, key, ROIname):
                try:
                    self.roi_names[str(key)] = ROIname
                except:
                    Warning.warn("Wrong key given in update_ROIname()")

            def update_nameSet(self, key, nameSet):
                try:
                    self.nameSet[str(key)] = nameSet
                except:
                    Warning.warn("Wrong key given in update_nameSet()")

            def update_nameSetInfo(self, key, nameSetInfo):
                try:
                    self.nameSetInfo[str(key)] = nameSetInfo
                except:
                    Warning.warn("Wrong key given in update_nameSetInfo()")
            
            def convert_to_LPS(self, data):
                """
                -------------------------------------------------------------------------
                DESCRIPTION:
                This function converts the given volume to LPS coordinates system. For 
                more details please refer here : https://www.slicer.org/wiki/Coordinate_systems 
                -------------------------------------------------------------------------
                INPUTS:
                - data : given volume data in RAS to be converted to LPS
                -------------------------------------------------------------------------
                OUTPUTS:
                - data in LPS.
                -------------------------------------------------------------------------
                """
                # flip x
                data = np.flip(data, 0)
                # flip y
                data = np.flip(data, 1)

                return data

            def get_ROI_from_path(self, ROI_path, ID):
                """
                -------------------------------------------------------------------------
                DESCRIPTION:
                This function extracts all ROI data from the given path for the given
                patient ID and updates all class attributes with the new extracted data.
                This method is called only once for NIFTI formats per patient.
                -------------------------------------------------------------------------
                INPUTS:
                - ROI_path : Path where the ROI data is stored
                - ID : The ID contains patient ID and the modality type, which makes it
                possible for the method to extract the right data.
                -------------------------------------------------------------------------
                OUTPUTS:
                - NO OUTPUTS.
                -------------------------------------------------------------------------
                """
                self.indexes = {}
                self.roi_names = {}
                self.nameSet = {}
                self.nameSetInfo = {}
                roi_index = 0
                list_of_patients = os.listdir(ROI_path)

                for file in list_of_patients:
                    # Load the patient's ROI nifti files :
                    if file.startswith(ID) and file.endswith('nii.gz') and 'ROI' in file.split("."):
                        roi = nib.load(ROI_path + "/" + file)
                        roi_data = self.convert_to_LPS(data=roi.get_fdata())
                        roi_name = file[file.find("(")+1:file.find(")")]
                        nameSet = file[file.find("_")+2:file.find("(")]
                        self.update_indexes(key=roi_index, indexes=np.nonzero(roi_data.flatten()))
                        self.update_nameSet(key=roi_index, nameSet=nameSet)
                        self.update_ROIname(key=roi_index, ROIname=roi_name)
                        roi_index += 1
