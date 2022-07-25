import csv
import json
import logging
import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import List, Union

import nibabel as nib
import numpy as np
import pandas as pd
import pydicom
import pydicom.errors
import pydicom.misc
import ray
from MEDimage.MEDimage import MEDimage
from MEDimage.utils.imref import imref3d
from tqdm import tqdm, trange

from ..processing.compute_suv_map import compute_suv_map
from ..processing.get_roi_from_indexes import get_roi_from_indexes
from ..utils.get_file_paths import get_file_paths
from ..utils.get_patient_names import get_patient_names
from ..utils.json_utils import load_json, save_json
from ..utils.save_MEDimage import save_MEDimage
from .process_dicom_scan_files import process_dicom_scan_files as pdsf


class DataManager(object):
    """Reads all the raw data (DICOM, NIfTI) content and organizes it in instances of the MEDimage class."""


    @dataclass
    class DICOM(object):
        """DICOM data management class that will organize data during the conversion to MEDimage class process"""
        stack_series_rs: List
        stack_path_rs: List
        stack_frame_rs: List
        cell_series_id: List
        cell_path_rs: List
        cell_path_images: List
        cell_frame_rs: List
        cell_frame_id: List


    @dataclass
    class NIfTI(object):
        """NIfTI data management class that will organize data during the conversion to MEDimage class process"""
        stack_path_images: List
        stack_path_roi: List
        stack_path_all: List


    @dataclass
    class Paths(object):
        """Paths management class that will organize the paths used in the processing"""
        _path_to_dicoms: List
        _path_to_niftis: List
        _path_csv: Union[Path, str]
        _path_save: Union[Path, str]
        _path_save_checks: Union[Path, str]
        _path_pre_checks_settings: Union[Path, str]

    def __init__(
            self, 
            path_to_dicoms: List = [],
            path_to_niftis: List = [],
            path_csv: Union[Path, str] = None,
            path_save: Union[Path, str] = None,
            path_save_checks: Union[Path, str] = None,
            path_pre_checks_settings: Union[Path, str] = None,
            save: bool = False,
            keep_instances: bool = True,
            n_batch: int = 2
    ) -> None:
        """Constructor of the class DataManager.

        Args:
            path_to_dicoms (Union[Path, str], optional): Full path to the starting directory
                where the DICOM data is located.
            path_to_niftis (Union[Path, str], optional): Full path to the starting directory
                where the NIfTI is located.
            path_csv (Union[Path, str], optional): Full path to the CSV file containing the scans info list.
            path_save (Union[Path, str], optional): Full path to the directory where to save all the MEDimage classes.
            path_save_checks(Union[Path, str], optional): Full path to the directory where to save all 
                the pre-radiomics checks analysis results.
            path_pre_checks_settings(Union[Path, str], optional): Full path to the JSON file of the pre-checks analysis
                parameters.
            save (bool, optional): True to save the MEDimage classes in `path_save`.
            keep_instances(bool, optional): If True, will keep the created MEDimage instances in the class instance.
            n_batch (int, optional): Numerical value specifying the number of batch to use in the
                parallel computations (use 0 for serial computation).
        
        Returns:
            None
        """
        # Convert all paths to Pathlib.Path
        if path_to_dicoms:
            path_to_dicoms = Path(path_to_dicoms)
        if path_to_niftis:
            path_to_niftis = Path(path_to_niftis)
        if path_csv:
            path_csv = Path(path_csv)
        if path_save:
            path_save = Path(path_save)
        if path_save_checks:
            path_save_checks = Path(path_save_checks)
        if path_pre_checks_settings:
            path_pre_checks_settings = Path(path_pre_checks_settings)

        self.paths = self.Paths(
                path_to_dicoms,
                path_to_niftis,
                path_csv,
                path_save,
                path_save_checks,
                path_pre_checks_settings,
        )
        self.save = save
        self.keep_instances = keep_instances
        self.n_batch = n_batch
        self.__dicom = self.DICOM(
                stack_series_rs=[],
                stack_path_rs=[],
                stack_frame_rs=[],
                cell_series_id=[],
                cell_path_rs=[],
                cell_path_images=[],
                cell_frame_rs=[],
                cell_frame_id=[]
        )
        self.__nifti = self.NIfTI(
                stack_path_images=[],
                stack_path_roi=[],
                stack_path_all=[]
        )
        self.instances = []
        self.path_to_objects = []
        self.summary = {}
        self.csv_data = None
        self.__studies = []
        self.__institutions = []
        self.__scans = []
        self.__warned = False

    def __find_uid_cell_index(self, uid: Union[str, List[str]], cell: List[str]) -> List: 
        """Finds the cell with the same `uid`. If not is present in `cell`, creates a new position
        in the `cell` for the new `uid`.

        Args:
            uid (Union[str, List[str]]):  Unique identifier of the Series to find.
            cell (List[str]): List of Unique identifiers of the Series.

        Returns:
            Union[List[str], str]: List or string of the uid  
        """
        return [len(cell)] if uid not in cell else[i for i, e in enumerate(cell) if e == uid]

    def __get_list_of_files(self, dir_name: str) -> List:
        """Gets all files in the given directory

        Args:
            dir_name (str): directory name

        Returns:
            List: List of all files in the directory
        """
        list_of_file = os.listdir(dir_name)
        all_files = list()
        for entry in list_of_file:
            full_path = os.path.join(dir_name, entry)
            if os.path.isdir(full_path):
                all_files = all_files + self.__get_list_of_files(full_path)
            else:
                all_files.append(full_path)

        return all_files

    def __get_MEDimage_name_save(self, MEDimg: MEDimage) -> str:
        """Returns the name that will be used to save the MEDimage instance, based on the values of the attributes.

        Args:
            MEDimg(MEDimage): A MEDimage class instance.
        
        Returns:
            str: String of the name save.
        """
        if MEDimg.format == 'nifti':
            series_description = MEDimg.type.split('scan')[0]
        elif MEDimg.format == 'dicom':
            try:
                series_description = MEDimg.dicomH[0].SeriesDescription.translate({ord(ch): '-' for ch in '/\\ ()&:*'})
            except:
                series_description = MEDimg.dicomH[0].Modality.translate({ord(ch): '-' for ch in '/\\ ()&:*'})
        else:
            raise ValueError("Invalid format in the given MEDimage instance, must be 'dicom' or 'nifti'")
        name_id = MEDimg.patientID.translate({ord(ch): '-' for ch in '/\\ ()&:*'})
        # final saving name
        name_complete = name_id + '__' + series_description + '.' + MEDimg.type + '.npy'
        return name_complete

    def __associate_rt_stuct(self) -> None:
        """Associates the imaging volumes to their mask using UIDs

        Returns:
            None
        """
        print('--> Associating all RT objects to imaging volumes')
        n_rs = len(self.__dicom.stack_path_rs)
        if n_rs:
            for i in trange(0, n_rs):
                try: # PUT ALL THE DICOM PATHS WITH THE SAME UID IN THE SAME PATH LIST
                    ind_series_id = self.__find_uid_cell_index(
                                                        self.__dicom.stack_series_rs[i], 
                                                        self.__dicom.cell_series_id)
                    for n in range(len(ind_series_id)):
                        self.__dicom.cell_path_rs[ind_series_id[n]] += [self.__dicom.stack_path_rs[i]]
                except:
                    ind_series_id = self.__find_uid_cell_index(
                                                        self.__dicom.stack_frame_rs[i], 
                                                        self.__dicom.cell_frame_id)
                    for n in range(len(ind_series_id)):
                        self.__dicom.cell_path_rs[ind_series_id[n]] += [self.__dicom.stack_path_rs[i]]
        print('DONE')

    def __read_all_dicoms(self) -> None:
        """Reads all the dicom files in the all the paths of the attribute `_path_to_dicoms`

        Returns:
            None
        """
        # SCANNING ALL FOLDERS IN INITIAL DIRECTORY
        print('\n--> Scanning all folders in initial directory...', end='')
        p = Path(self.paths._path_to_dicoms)
        e_rglob = '*.[!xlsx,!xls,!py,!.DS_Store,!csv,!.,!txt,!..,!TXT,!npy,!m,!CT.npy]*'

        # EXTRACT ALL FILES IN THE PATH TO DICOMS
        if self.paths._path_to_dicoms.is_dir():
            stack_folder_temp = list(p.rglob(e_rglob))
            stack_folder = [x for x in stack_folder_temp if not x.is_dir()]
        elif str(self.paths._path_to_dicoms).find('json') != -1:
            with open(self.paths._path_to_dicoms) as f:
                data = json.load(f)
                for value in data.values():
                    stack_folder_temp = value
            directory_name = str(stack_folder_temp).replace("'", '').replace('[', '').replace(']', '')
            stack_folder = self.__get_list_of_files(directory_name)
        else:
            raise ValueError("The given dicom folder path either doesn't exist or not a folder.")
        # READ ALL DICOM FILES AND UPDATE ATTRIBUTES FOR FURTHER PROCESSING
        for file in tqdm(stack_folder):
            if pydicom.misc.is_dicom(file):
                try:
                    info = pydicom.dcmread(str(file))
                    if info.Modality in ['MR', 'PT', 'CT']:
                        ind_series_id = self.__find_uid_cell_index(
                                                        info.SeriesInstanceUID, 
                                                        self.__dicom.cell_series_id)[0]
                        if ind_series_id == len(self.__dicom.cell_series_id):  # New volume
                            self.__dicom.cell_series_id = self.__dicom.cell_series_id + [info.SeriesInstanceUID]
                            self.__dicom.cell_frame_id += [info.FrameOfReferenceUID]
                            self.__dicom.cell_path_images += [[]]
                            self.__dicom.cell_path_rs = self.__dicom.cell_path_rs + [[]]
                        self.__dicom.cell_path_images[ind_series_id] += [file]
                    elif info.Modality == 'RTSTRUCT':
                        self.__dicom.stack_path_rs += [file]
                        try:
                            series_uid = info.ReferencedFrameOfReferenceSequence[
                                        0].RTReferencedStudySequence[
                                        0].RTReferencedSeriesSequence[
                                        0].SeriesInstanceUID
                        except:
                            series_uid = 'NotFound'
                        self.__dicom.stack_series_rs += [series_uid]
                        try:
                            frame_uid = info.ReferencedFrameOfReferenceSequence[0].FrameOfReferenceUID
                        except:
                            frame_uid = info.FrameOfReferenceUID
                        self.__dicom.stack_frame_rs += [frame_uid]

                except Exception as e:
                    print(f'Error while reading: {file}, error: {e}\n')
                    continue
        print('DONE')

        # ASSOCIATE ALL VOLUMES TO THEIR MASK
        self.__associate_rt_stuct()

    def process_all_dicoms(self) -> List[MEDimage]:
        """This function reads the DICOM content of all the sub-folder tree of a starting directory defined by
        `path_to_dicoms`. It then organizes the data (files throughout the starting directory are associated by
        'SeriesInstanceUID') in the MEDimage class including the region of  interest (ROI) defined by an
        associated RTstruct. All MEDimage classes hereby created are saved in `path_save` with a name
        varying with every scan.

        Returns:
            List[MEDimage]: List of MEDimage instances.
        """
        ray.init(local_mode=True, include_dashboard=True)

        print('--> Reading all DICOM objects to create MEDimage classes')
        self.__read_all_dicoms()

        n_scans = len(self.__dicom.cell_path_images)
        if self.n_batch is None:
            n_batch = 1
        elif n_scans < self.n_batch:
            n_batch = n_scans
        else:
            n_batch = self.n_batch

        # Distribute the first tasks to all workers
        ids = [pdsf.remote(
                        self.__dicom.cell_path_images[i], 
                        self.__dicom.cell_path_rs[i], 
                        self.paths._path_save,
                        self.save)
            for i in range(n_batch)]
        # Update the path to the created instances
        for instance in ray.get(ids):
            name_save = self.__get_MEDimage_name_save(instance)
            if self.paths._path_save:
                self.path_to_objects.append(str(self.paths._path_save / name_save))
            # Update processing summary
            if name_save.split('_')[0].count('-') >= 2:
                scan_type = name_save[name_save.find('__')+2 : name_save.find('.')]
                if name_save.split('-')[0] not in self.__studies:
                    self.__studies.append(name_save.split('-')[0])  # add new study
                if name_save.split('-')[1] not in self.__institutions:
                    self.__institutions.append(name_save.split('-')[1])  # add new study
                if name_save.split('-')[0] not in self.summary:
                    self.summary[name_save.split('-')[0]] = {}
                if name_save.split('-')[1] not  in self.summary[name_save.split('-')[0]]:
                    self.summary[name_save.split('-')[0]][name_save.split('-')[1]] = {}  # add new institution
                if scan_type not in self.__scans:
                    self.__scans.append(scan_type)
                if scan_type not in self.summary[name_save.split('-')[0]][name_save.split('-')[1]]:
                    self.summary[name_save.split('-')[0]][name_save.split('-')[1]][scan_type] = []
                self.summary[name_save.split('-')[0]][name_save.split('-')[1]][scan_type].append(name_save)
            else:
                logging.warning(f"The patient ID of the following file: {name_save} does not respect the MEDimage "\
                    "naming convention 'study-institution-id' (Ex: Glioma-TCGA-001)")

        nb_job_left = n_scans - n_batch

        # Get MEDimage instances
        if len(self.instances)>10 and not self.__warned:
            # User cannot save over 10 instances in the class
            warnings.warn("You have more than 10 MEDimage objects saved in the current DataManager instance, \
                the rest of the instances will/can be saved locally only.")
            self.__warned = True
        elif self.keep_instances:
            self.instances.extend(ray.get(ids))

        # Distribute the remaining tasks
        for _ in trange(n_scans):
            _, not_ready = ray.wait(ids, num_returns=1)
            ids = not_ready
            if nb_job_left > 0:
                idx = n_scans - nb_job_left
                ids.extend([pdsf.remote(self.__dicom.cell_path_images[idx], 
                                        self.__dicom.cell_path_rs[idx], 
                                        self.paths._path_save,
                                        self.save)])
                nb_job_left -= 1

        # Update the path to the created instances
        for instance in ray.get(ids):
            name_save = self.__get_MEDimage_name_save(instance)
            if self.paths._path_save:
                self.path_to_objects.extend(str(self.paths._path_save / name_save))
            # Update processing summary
            if name_save.split('_')[0].count('-') >= 2:
                scan_type = name_save[name_save.find('__')+2 : name_save.find('.')]
                if name_save.split('-')[0] not in self.__studies:
                    self.__studies.append(name_save.split('-')[0])  # add new study
                if name_save.split('-')[1] not in self.__institutions:
                    self.__institutions.append(name_save.split('-')[1])  # add new study
                if name_save.split('-')[0] not in self.summary:
                    self.summary[name_save.split('-')[0]] = {}
                if name_save.split('-')[1] not  in self.summary[name_save.split('-')[0]]:
                    self.summary[name_save.split('-')[0]][name_save.split('-')[1]] = {}  # add new institution
                if scan_type not in self.__scans:
                    self.__scans.append(scan_type)
                if scan_type not in self.summary[name_save.split('-')[0]][name_save.split('-')[1]]:
                    self.summary[name_save.split('-')[0]][name_save.split('-')[1]][scan_type] = []
                self.summary[name_save.split('-')[0]][name_save.split('-')[1]][scan_type].append(name_save)
            else:
                logging.warning(f"The patient ID of the following file: {name_save} does not respect the MEDimage "\
                    "naming convention 'study-institution-id' (Ex: Glioma-TCGA-001)")

        # Get MEDimage instances
        if len(self.instances)>10 and not self.__warned:
            warnings.warn("You have more than 10 MEDimage objects saved in the current DataManager instance, \
                the rest of the instances will/can be saved locally only.")
            self.__warned = True
        else:
            self.instances.extend(ray.get(ids))
        print('DONE')

        return self.instances

    def __read_all_niftis(self) -> None:
        """Reads all files in the initial path and organizes other path to images and roi
        in the class attributes.

        Returns:
            None.
        """
        print('\n--> Scanning all folders in initial directory')
        p = Path(self.paths._path_to_niftis)
        e_rglob1 = '*.nii'
        e_rglob2 = '*.nii.gz'

        # EXTRACT ALL FILES IN THE PATH TO DICOMS
        if p.is_dir():
            self.__nifti.stack_path_all = list(p.rglob(e_rglob1))
            self.__nifti.stack_path_all.extend(list(p.rglob(e_rglob2)))
        else:
            raise TypeError("`_path_to_niftis` must be a path to a directory")

        all_niftis = list(self.__nifti.stack_path_all)
        for i in trange(0, len(all_niftis)):
            if 'ROI' in all_niftis[i].name.split("."):
                self.__nifti.stack_path_roi.append(all_niftis[i])
            else:
                self.__nifti.stack_path_images.append(all_niftis[i])
        print('DONE')

    def __associate_roi_to_image(self, image_file: Union[Path, str], MEDimg: MEDimage) -> MEDimage:
        """Extracts all ROI data from the given path for the given patient ID and updates all class attributes with
        the new extracted data.

        Args:
            image_file(Union[Path, str]): Path to the ROI data.
            MEDimg (MEDimage): MEDimage class instance that will hold the data. 

        Returns:
            MEDimage: Returns a MEDimage instance with updated roi attributes.
        """
        image_file = Path(image_file)
        roi_index = 0

        for file in self.__nifti.stack_path_roi:
            _id = image_file.name.split("(")[0] # id is PatientID__ImagingScanName
            # Load the patient's ROI nifti files:
            if file.name.startswith(_id) and 'ROI' in file.name.split("."):
                roi = nib.load(file)
                roi_data = MEDimg.scan.ROI.convert_to_LPS(data=roi.get_fdata())
                roi_name = file.name[file.name.find("(") + 1 : file.name.find(")")]
                name_set = file.name[file.name.find("_") + 2 : file.name.find("(")]
                MEDimg.scan.ROI.update_indexes(key=roi_index, indexes=np.nonzero(roi_data.flatten()))
                MEDimg.scan.ROI.update_name_set(key=roi_index, nameSet=name_set)
                MEDimg.scan.ROI.update_roi_name(key=roi_index, roi_name=roi_name)
                roi_index += 1
        return MEDimg

    def __associate_spatialRef(self, nifti_file: Union[Path, str], MEDimg: MEDimage) -> MEDimage:
        """Computes the imref3d spatialRef using a NIFTI file and updates the spatialRef attribute.

        Args:
            nifti_file(Union[Path, str]): Path to the nifti data.
            MEDimg (MEDimage): MEDimage class instance that will hold the data.

        Returns:
            MEDimage: Returns a MEDimage instance with updated spatialRef attribute.
        """
        # Loading the nifti file :
        nifti = nib.load(nifti_file)
        nifti_data = MEDimg.scan.volume.data

        # spatialRef Creation
        pixel_x = abs(nifti.affine[0, 0])
        pixel_y = abs(nifti.affine[1, 1])
        slices = abs(nifti.affine[2, 2])
        min_grid = nifti.affine[:3, 3] * [-1.0, -1.0, 1.0] # x and y are flipped
        min_x_grid = min_grid[0]
        min_y_grid = min_grid[1]
        min_z_grid = min_grid[2]
        size_image = np.shape(nifti_data)
        spatialRef = imref3d(size_image, abs(pixel_x), abs(pixel_y), abs(slices))
        spatialRef.XWorldLimits = (np.array(spatialRef.XWorldLimits) -
                                (spatialRef.XWorldLimits[0] -
                                    (min_x_grid-pixel_x/2))
                                ).tolist()
        spatialRef.YWorldLimits = (np.array(spatialRef.YWorldLimits) -
                                (spatialRef.YWorldLimits[0] -
                                    (min_y_grid-pixel_y/2))
                                ).tolist()
        spatialRef.ZWorldLimits = (np.array(spatialRef.ZWorldLimits) -
                                (spatialRef.ZWorldLimits[0] -
                                    (min_z_grid-slices/2))
                                ).tolist()

        # Converting the results into lists
        spatialRef.ImageSize = spatialRef.ImageSize.tolist()
        spatialRef.XIntrinsicLimits = spatialRef.XIntrinsicLimits.tolist()
        spatialRef.YIntrinsicLimits = spatialRef.YIntrinsicLimits.tolist()
        spatialRef.ZIntrinsicLimits = spatialRef.ZIntrinsicLimits.tolist()

        # update spatialRef in the volume sub-class
        MEDimg.scan.volume.update_spatialRef(spatialRef)

        return MEDimg

    def process_all(self) -> None:
        """Processes both DICOM & NIfTI content to create MEDimage classes
        """
        self.process_all_dicoms()
        self.process_all_niftis()

    def process_all_niftis(self) -> List[MEDimage]:
        """This function reads the NIfTI content of all the sub-folder tree of a starting directory. 
        It then organizes the data in the MEDimage class including the region of  interest (ROI)
        defined by an associated mask file. All MEDimage classes hereby created are saved in a specific path
        with a name specific name varying with every scan.

        Args:
            None.

        Returns:
            List[MEDimage]: List of MEDimage instances.
        """
        self.__read_all_niftis()
        print('--> Reading all NIfTI objects (imaging volumes & masks) to create MEDimage classes')
        for file in tqdm(self.__nifti.stack_path_images):
            # User cannot save over 10 instances in the class
            if len(self.instances)>10 and not self.__warned:
                warnings.warn("You have more than 10 MEDimage objects saved in the current DataManager instance, \
                    the rest of the instances will/can be saved locally only.")
                self.__warned = True
            # INITIALIZE MEDimage INSTANCE AND UPDATE ATTRIBUTES
            MEDimg = MEDimage()
            MEDimg.patientID = os.path.basename(file).split("_")[0]
            MEDimg.type = os.path.basename(file).split(".")[-3]
            MEDimg.format = "nifti"
            MEDimg.scan.set_orientation(orientation="Axial")
            MEDimg.scan.set_patientPosition(patientPosition="HFS")
            MEDimg.scan.volume.data = nib.load(file).get_fdata()
            # RAS to LPS
            MEDimg.scan.volume.convert_to_LPS()
            MEDimg.scan.volume.scan_rot = None
            # UPDATE spatialRef
            self.__associate_spatialRef(file, MEDimg)
            # GET ROI
            MEDimage_instance = self.__associate_roi_to_image(file, MEDimg)
            if self.keep_instances:
                self.instances.append(MEDimage_instance)
            if self.save and self.paths._path_save:
                save_MEDimage(MEDimg, MEDimg.type.split('scan')[0], self.paths._path_save)
            # Update the path to the created instances
            name_save = self.__get_MEDimage_name_save(MEDimage_instance)
            if self.paths._path_save:
                self.path_to_objects.append(str(self.paths._path_save / name_save))
            # Update processing summary
            if name_save.split('_')[0].count('-') >= 2:
                scan_type = name_save[name_save.find('__')+2 : name_save.find('.')]
                if name_save.split('-')[0] not in self.__studies:
                    self.__studies.append(name_save.split('-')[0])  # add new study
                if name_save.split('-')[1] not in self.__institutions:
                    self.__institutions.append(name_save.split('-')[1])  # add new institution
                if name_save.split('-')[0] not in self.summary:
                    self.summary[name_save.split('-')[0]] = {}  # add new study to summary
                if name_save.split('-')[1] not  in self.summary[name_save.split('-')[0]]:
                    self.summary[name_save.split('-')[0]][name_save.split('-')[1]] = {}  # add new institution
                if scan_type not in self.__scans:
                    self.__scans.append(scan_type)
                if scan_type not in self.summary[name_save.split('-')[0]][name_save.split('-')[1]]:
                    self.summary[name_save.split('-')[0]][name_save.split('-')[1]][scan_type] = []
                self.summary[name_save.split('-')[0]][name_save.split('-')[1]][scan_type].append(name_save)
            else:
                logging.warning(f"The patient ID of the following file: {name_save} does not respect the MEDimage "\
                    "naming convention 'study-institution-id' (Ex: Glioma-TCGA-001)")
        print('DONE')
        return self.instances

    def update_from_csv(self, path_csv: Union[str, Path] = None) -> None:
        """Updates the class from a given CSV and summarizes the processed scans again according to it.

        Args:
            path_csv(optional, Union[str, Path]): Path to a csv file, if not given, will check
                for csv info in the class attributes.
        
        Returns:
            None
        """
        if not (path_csv or self.paths._path_csv):
            print('No csv provided, no updates will be made')
        else:
            # Extract roi type label from csv file name
            name_csv = self.paths._path_csv.name
            roi_type_label = name_csv[name_csv.find('_')+1 : name_csv.find('.')]

            # Create a dictionary
            csv_data = {}
            csv_data[roi_type_label] = {}
            # Open a csv reader using DictReader
            with open(self.paths._path_csv, encoding='utf-8') as csvf:
                csv_reader = csv.DictReader(csvf)
                # Convert each row into a dictionary
                # and add it to data
                for rows in csv_reader:
                    # Assuming a column named 'No' to
                    # be the primary key
                    key = rows['PatientID']
                    csv_data[roi_type_label][key] = rows
            
            self.csv_data = csv_data
            self.summarize()

    def summarize(self):
        """Creates and shows a summary of processed scans organized by study, institution, scan type and roi type

        Args:
            None
        Returns:
            None
        """
        def count_scans(summary):
            count = 0
            for study in summary:
                for institution in summary[study]:
                    count += len(summary[study][institution])
            return count

        summary_df = pd.DataFrame(columns=['study', 'institution', 'scan_type', 'roi_type', 'count'])

        for study in self.summary:
            summary_df = summary_df.append({
                                        'study': study,
                                        'institution': "",
                                        'scan_type': "",
                                        'roi_type': "",
                                        'count' : count_scans(self.summary)
                                        }, ignore_index=True)
            for institution in self.summary[study]:
                summary_df = summary_df.append({
                                            'study': study,
                                            'institution': institution,
                                            'scan_type': "",
                                            'roi_type': "",
                                            'count' : len(self.summary[study][institution])
                                            }, ignore_index=True)
                for scan in self.summary[study][institution]:
                    summary_df = summary_df.append({
                                                'study': study,
                                                'institution': institution,
                                                'scan_type': scan,
                                                'roi_type': "",
                                                'count' : len(self.summary[study][institution][scan])
                                                }, ignore_index=True)
                    if self.csv_data:
                        patient_id = scan.split('_')[0]
                        for roi_type in self.csv_data:
                            if patient_id in self.csv_data[roi_type]:
                                break
                            summary_df = summary_df.append({
                                                'study': study,
                                                'institution': institution,
                                                'scan_type': scan,
                                                'roi_type': roi_type,
                                                'count' : 1
                                                }, ignore_index=True)
        print(summary_df.to_markdown(index=False))

    def pre_checks_init(
            self, 
            path_save_checks: Union[Path, str], 
            path_csv: Union[Path, str],
            force: bool = False
        ) -> None:
        """Initializes all the class attributes needed to run pre-radiomics check methods.

        This method is useful to make sure all the important attributes are ready before running
        radiomics pre-checks, in case these attributes were not specified during the class initialization.

        Args:
            path_save_checks(Union[Path, str]): Path to where the radiomics checks are gonna be saved.
            path_csv(Union[Path, str]): Path to the csv file that will be used to read scans.
            force(bool, optional): If True, will replace the values of the existing attributes and
                will skip (keep the originals) if false.
        """
        if self.paths._path_save_checks and force:
            self.paths._path_save_checks = path_save_checks
        if self.paths._path_csv and force:
            self.paths._path_csv = path_csv

    def __pre_radiomics_checks_dimensions(
                                        self, 
                                        wildcards_dimensions: List[str] = [], 
                                        use_instances: bool = True
                                        ) -> None:
        """Finds proper dimensions options for radiomics analyses for a group of scans

        Args:
            wildcards_dimensions(List[str], optional): List of wildcards that determines the scans 
                that will be analyzed. You can learn more about wildcards in
                :ref:`this link <https://www.linuxtechtips.com/2013/11/how-wildcards-work-in-linux-and-unix.html>`.
            use_instances(bool, optional): If True will use the instances of the MEDimage class saved in DataManager
                for the analysis. If False, will analyze scans in the path where the instances were saved.
        
        Returns:
            None.
        """
        xy_dim = {
            "data": [],
            "mean": [],
            "median": [],
            "std": [],
            "min": [],
            "max": [],
            "p5": [],
            "p95": []
        }
        z_dim = {
            "data": [],
            "mean": [],
            "median": [],
            "std": [],
            "min": [],
            "max": [],
            "p5": [],
            "p95": []
        }

        if len(wildcards_dimensions) == 0 and not use_instances:
            print("Wildcard is empty and instances use is not allowed, the pre-checks will be aborted")
            return

        # TODO: seperate by studies and scan type (MRscan, CTscan...)
        # TODO: Two summaries (df, list of names saves) -> 
        # name_save = name_save(ROI) : Glioma-Huashan-001__T1.MRscan.npy({GTV})
        file_paths = list()
        for w in range(len(wildcards_dimensions)):
            wildcard = wildcards_dimensions[w]
            if use_instances:
                wildcard = str(wildcard).replace('*', '')
                study, scan_type = wildcard.split('.')[0:2]
                n_instances = len(self.instances)
                xy_dim["data"] = np.zeros((n_instances, 1))
                xy_dim["data"] = np.multiply(xy_dim["data"], np.nan)
                z_dim["data"] = np.zeros((n_instances, 1))
                z_dim["data"] = np.multiply(z_dim["data"], np.nan)
                for i in tqdm(range(len(self.instances))):
                    try:
                        MEDimg = self.instances[i]
                        if MEDimg.patientID.split('-')[0] == study and MEDimg.type == scan_type:
                            xy_dim["data"][i] = MEDimg.scan.volume.spatialRef.PixelExtentInWorldX
                            z_dim["data"][i]  = MEDimg.scan.volume.spatialRef.PixelExtentInWorldZ
                        else:
                            continue
                    except Exception as e:
                        print(e)
                # Running analysis
                xy_dim["data"] = np.concatenate(xy_dim["data"])
                xy_dim["mean"] = np.mean(xy_dim["data"][~np.isnan(xy_dim["data"])])
                xy_dim["median"] = np.median(xy_dim["data"][~np.isnan(xy_dim["data"])])
                xy_dim["std"] = np.std(xy_dim["data"][~np.isnan(xy_dim["data"])])
                xy_dim["min"] = np.min(xy_dim["data"][~np.isnan(xy_dim["data"])])
                xy_dim["max"] = np.max(xy_dim["data"][~np.isnan(xy_dim["data"])])
                xy_dim["p5"] = np.percentile(xy_dim["data"][~np.isnan(xy_dim["data"])], 5)
                xy_dim["p95"] = np.percentile(xy_dim["data"][~np.isnan(xy_dim["data"])], 95)
                z_dim["mean"] = np.mean(z_dim["data"][~np.isnan(z_dim["data"])])
                z_dim["median"] = np.median(z_dim["data"][~np.isnan(z_dim["data"])])
                z_dim["std"] = np.std(z_dim["data"][~np.isnan(z_dim["data"])])
                z_dim["min"] = np.min(z_dim["data"][~np.isnan(z_dim["data"])])
                z_dim["max"] = np.max(z_dim["data"][~np.isnan(z_dim["data"])])
                z_dim["p5"] = np.percentile(z_dim["data"][~np.isnan(z_dim["data"])], 5)
                z_dim["p95"] = np.percentile(z_dim["data"][~np.isnan(z_dim["data"])], 95)
                xy_dim["data"] = xy_dim["data"].tolist()
                z_dim["data"] = z_dim["data"].tolist()

                # Saving files using wildcard for name
                wildcard = str(wildcard).replace('*', '').replace('.npy', '.json')
                save_json(self.paths._path_save_checks / ('xyDim_' + wildcard), xy_dim)
                save_json(self.paths._path_save_checks / ('zDim_' + wildcard), z_dim)
            else:
                file_paths = get_file_paths(self.paths._path_save, wildcard)
                n_files = len(file_paths)
                xy_dim["data"] = np.zeros((n_files, 1))
                xy_dim["data"] = np.multiply(xy_dim["data"], np.nan)
                z_dim["data"] = np.zeros((n_files, 1))
                z_dim["data"] = np.multiply(z_dim["data"], np.nan)
                for f in tqdm(range(len(file_paths))):
                    try:
                        MEDimg = np.load(file_paths[0], allow_pickle=True)
                        xy_dim["data"][f] = MEDimg.scan.volume.spatialRef.PixelExtentInWorldX
                        z_dim["data"][f]  = MEDimg.scan.volume.spatialRef.PixelExtentInWorldZ
                    except Exception as e:
                        print(e)

                xy_dim["data"] = np.concatenate(xy_dim["data"])
                xy_dim["mean"] = np.mean(xy_dim["data"][~np.isnan(xy_dim["data"])])
                xy_dim["median"] = np.median(xy_dim["data"][~np.isnan(xy_dim["data"])])
                xy_dim["std"] = np.std(xy_dim["data"][~np.isnan(xy_dim["data"])])
                xy_dim["min"] = np.min(xy_dim["data"][~np.isnan(xy_dim["data"])])
                xy_dim["max"] = np.max(xy_dim["data"][~np.isnan(xy_dim["data"])])
                xy_dim["p5"] = np.percentile(xy_dim["data"][~np.isnan(xy_dim["data"])], 5)
                xy_dim["p95"] = np.percentile(xy_dim["data"][~np.isnan(xy_dim["data"])], 95)
                z_dim["mean"] = np.mean(z_dim["data"][~np.isnan(z_dim["data"])])
                z_dim["median"] = np.median(z_dim["data"][~np.isnan(z_dim["data"])])
                z_dim["std"] = np.std(z_dim["data"][~np.isnan(z_dim["data"])])
                z_dim["min"] = np.min(z_dim["data"][~np.isnan(z_dim["data"])])
                z_dim["max"] = np.max(z_dim["data"][~np.isnan(z_dim["data"])])
                z_dim["p5"] = np.percentile(z_dim["data"][~np.isnan(z_dim["data"])], 5)
                z_dim["p95"] = np.percentile(z_dim["data"][~np.isnan(z_dim["data"])], 95)
                xy_dim["data"] = xy_dim["data"].tolist()
                z_dim["data"] = z_dim["data"].tolist()

                # Saving files using wildcard for name
                wildcard = str(wildcard).replace('*', '').replace('.npy', '.json')
                save_json(self.paths._path_save_checks / ('xyDim_' + wildcard), xy_dim)
                save_json(self.paths._path_save_checks / ('zDim_' + wildcard), z_dim)

    def __pre_radiomics_checks_window(
            self, 
            wildcards_window: List = [], 
            use_instances: bool = True,
            path_csv: Union[str, Path] = None
        ) -> None:
        """Finds proper re-segmentation ranges options for radiomics analyses for a group of scans

        Args:
            wildcards_window(List[str], optional): List of wildcards that determines the scans 
            that will be analyzed. You can learn more about wildcards in
                :ref:`this link <https://www.linuxtechtips.com/2013/11/how-wildcards-work-in-linux-and-unix.html>`.
            use_instances(bool, optional): If True will use the instances of the MEDimage class saved in DataManager
                for the analysis. If False, will analyze scans in the path where the instances were saved.
            path_csv(Union[str, Path], optional): Path to a csv file containing a list of the scans that will be
                analyzed (a CSV file for a single ROI type).
        
        Returns:
            None.
        """
        roi_data= {
            "data": [],
            "mean": [],
            "median": [],
            "std": [],
            "min": [],
            "max": [],
            "p5": [],
            "p95": []
        }

        if len(wildcards_window) == 0:
            print("Wilcards is empty")
            return
        if path_csv:
            self.paths._path_csv = Path(path_csv)
        roi_table = pd.read_csv(self.paths._path_csv)
        roi_names = [[], [], []]
        roi_names[0] = roi_table['PatientID']
        roi_names[1] = roi_table['ImagingScanName']
        roi_names[2] = roi_table['ImagingModality']
        patient_names = get_patient_names(roi_names)

        temp_val = []
        temp = []
        file_paths = []
        for w in range(len(wildcards_window)):
            wildcard = wildcards_window[w]
            if not use_instances:
                file_paths = get_file_paths(self.paths._path_save, wildcard)
                n_files = len(file_paths)
                for f in tqdm(range(n_files)):
                    file = file_paths[f]
                    _, filename = os.path.split(file)
                    filename, ext = os.path.splitext(filename)
                    patient_name = filename + ext
                    try:
                        MEDimg = np.load(file, allow_pickle=True)
                        if re.search('PTscan', wildcard):
                            MEDimg.scan.volume.data = compute_suv_map(
                                                        np.double(MEDimg.scan.volume.data), 
                                                        MEDimg.dicomH[2])
                        patient_names = pd.Index(patient_names)
                        ind_roi = patient_names.get_loc(patient_name)
                        name_roi = roi_table.loc[ind_roi][3]
                        vol_obj_init, roi_obj_init = get_roi_from_indexes(MEDimg, name_roi, 'box')
                        temp = vol_obj_init.data[roi_obj_init.data == 1]
                        temp_val.append(len(temp))
                        roi_data["data"].append(np.zeros(shape=(n_files, temp_val[f])))
                        roi_data["data"][f] = temp
                    except:
                        roi_data["data"][f] = []
            else:
                for i in tqdm(range(len(self.instances))):
                    wildcard = str(wildcard).replace('*', '')
                    study, scan_type = wildcard.split('.')[0:2]
                    MEDimg = self.instances[i]
                    patient_name = self.__get_MEDimage_name_save(MEDimg)
                    if MEDimg.patientID.split('-')[0] == study and MEDimg.type == scan_type:
                        try:
                            if re.search('PTscan', wildcard):
                                MEDimg.scan.volume.data = compute_suv_map(np.double(MEDimg.scan.volume.data), 
                                                                            MEDimg.dicomH[2])
                            patient_names = pd.Index(patient_names)
                            ind_roi = patient_names.get_loc(patient_name)
                            name_roi = roi_table.loc[ind_roi][3]
                            vol_obj_init, roi_obj_init = get_roi_from_indexes(MEDimg, name_roi, 'box')
                            temp = vol_obj_init.data[roi_obj_init.data == 1]
                            temp_val.append(len(temp))
                            roi_data["data"].append(np.zeros(shape=(len(self.instances), temp_val[i])))
                            roi_data["data"][i] = temp
                        except:
                            roi_data["data"].append([])
                            roi_data["data"][i] = []
                    else:
                        roi_data["data"].append([])
                        continue

        roi_data["data"] = np.concatenate(roi_data["data"])
        roi_data["mean"] = np.mean(roi_data["data"][~np.isnan(roi_data["data"])])
        roi_data["median"] = np.median(roi_data["data"][~np.isnan(roi_data["data"])])
        roi_data["std"] = np.std(roi_data["data"][~np.isnan(roi_data["data"])])
        roi_data["min"] = np.min(roi_data["data"][~np.isnan(roi_data["data"])])
        roi_data["max"] = np.max(roi_data["data"][~np.isnan(roi_data["data"])])
        roi_data["p5"] = np.percentile(roi_data["data"][~np.isnan(roi_data["data"])], 5)
        roi_data["p95"] = np.percentile(roi_data["data"][~np.isnan(roi_data["data"])], 95)
        roi_data["data"] = roi_data["data"].tolist()

        # save final checks
        wildcard = str(wildcard).replace('*', '').replace('.npy', '.json')
        save_json(self.paths._path_save_checks / ('roi_data_' + wildcard), roi_data)

    def pre_radiomics_checks(self, 
                            wildcards_dimensions: List = [],
                            wildcards_window: List = [],
                            use_instances: bool = True,
                            path_csv: Union[str, Path] = None) -> None:
        """Finds proper dimension and re-segmentation ranges options for radiomics analyses. 

        The resulting files from this method can then be analyzed and used to set up radiomics 
        parameters options in computation methods.

        Args:
            wildcards_dimensions(List[str], optional): List of wildcards that determines the scans 
                that will be analyzed. You can learn more about wildcards in
                :ref:`this link <https://www.linuxtechtips.com/2013/11/how-wildcards-work-in-linux-and-unix.html>`.
            wildcards_window(List[str], optional): List of wildcards that determines the scans 
                that will be analyzed. You can learn more about wildcards in
                :ref:`this link <https://www.linuxtechtips.com/2013/11/how-wildcards-work-in-linux-and-unix.html>`.
            use_instances(bool, optional): If True will use the instances of the MEDimage class saved in DataManager
                for the analysis. If False, will analyze scans in the path where the instances were saved.
            path_csv(Union[str, Path], optional): Path to a csv file containing a list of the scans that will be
                analyzed (a CSV file for a single ROI type).

        Returns:
            None
        """
        # Initialization
        path_study = Path.cwd()

        # Load params
        settings = self.paths._path_pre_checks_settings
        settings = load_json(settings)
        settings = settings['pre_radiomics_checks']  

        # Setting up paths
        if 'path_save_checks' in settings and settings['path_save_checks']:
            self.paths._path_save_checks = Path(settings['path_save_checks']) 
        if 'path_csv' in settings and settings['path_csv']:
            self.paths._path_csv = Path(settings['path_csv']) 

        # Wildcards of groups of files to analyze for dimensions in path_data.
        # See for example: https://www.linuxtechtips.com/2013/11/how-wildcards-work-in-linux-and-unix.html
        # Keep the cell empty if no dimension checks are to be performed.
        if not wildcards_dimensions:
            wildcards_dimensions = []
            for i in range(len(settings['wildcards_dimensions'])):
                wildcards_dimensions.append(settings['wildcards_dimensions'][i])

        # ROI intensity window checks params
        if not wildcards_window:
            wildcards_window = []
            for i in range(len(settings['wildcards_window'])):
                wildcards_window.append(settings['wildcards_window'][i])

        # PRE-RADIOMICS CHECKS
        if not self.paths._path_save_checks:
            os.mkdir(path_study / 'checks')
            self.paths._path_save_checks = Path(path_study / 'checks')
        else:
            if self.paths._path_save_checks.name != 'checks' and (self.paths._path_save_checks / 'checks').exists():
                self.paths._path_save_checks /= 'checks'
            else:
                self.paths._path_save_checks /= 'checks'
                os.mkdir(self.paths._path_save_checks)

        start = time()
        print('\n\n************************* PRE-RADIOMICS CHECKS *************************', end='')

        # 1. PRE-RADIOMICS CHECKS -- DIMENSIONS
        start1 = time()
        print('\n--> PRE-RADIOMICS CHECKS -- DIMENSIONS ... ', end='')
        self.__pre_radiomics_checks_dimensions(wildcards_dimensions, use_instances)
        print('DONE', end='')
        time1 = f"{time() - start1:.2f}"
        print(f'\nElapsed time: {time1} sec', end='')

        # 2. PRE-RADIOMICS CHECKS - WINDOW
        start2 = time()
        print('\n\n--> PRE-RADIOMICS CHECKS -- WINDOW ... \n', end='')
        self.__pre_radiomics_checks_window(wildcards_window, use_instances, path_csv)
        print('DONE', end='')
        time2 = f"{time() - start2:.2f}"
        print(f'\nElapsed time: {time2} sec', end='')

        time_elapsed = f"{time() - start:.2f}"
        print(f'\n\n--> TOTAL TIME FOR PRE-RADIOMICS CHECKS: {time_elapsed} seconds')
        print('-------------------------------------------------------------------------------------')
