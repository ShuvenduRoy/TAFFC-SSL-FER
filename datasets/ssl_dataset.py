import torch
from torchvision.datasets import VisionDataset
from torchvision.datasets.folder import make_dataset
from torchvision.datasets.utils import list_dir
from torchvision.datasets.vision import VisionDataset
from torchvision.datasets.video_utils import VideoClips
from .data_utils import split_ssl_data, sample_labeled_data
from .dataset import BasicDataset
from collections import Counter
import torchvision
import numpy as np
from torchvision import transforms
import json
import os
import torch.nn as nn
import random
from .augmentation.randaugment import RandAugment

import gc
import sys
import copy
from PIL import Image

mean, std = {}, {}
mean['cifar10'] = [x / 255 for x in [125.3, 123.0, 113.9]]
mean['cifar100'] = [x / 255 for x in [129.3, 124.1, 112.4]]
mean['svhn'] = [0.4380, 0.4440, 0.4730]
mean['stl10'] = [x / 255 for x in [112.4, 109.1, 98.6]]
mean['imagenet'] = [0.485, 0.456, 0.406]
mean['FER13'] = [0.485, 0.456, 0.406]
mean['KDEF'] = [0.485, 0.456, 0.406]
mean['DDCF'] = [0.485, 0.456, 0.406]
mean['RAF'] = [0.485, 0.456, 0.406]
mean['AffectNet'] = [0.485, 0.456, 0.406]
mean['CelebA'] = [0.485, 0.456, 0.406]
mean['SemiInat'] = [0.4732, 0.4828, 0.3779]

std['cifar10'] = [x / 255 for x in [63.0, 62.1, 66.7]]
std['cifar100'] = [x / 255 for x in [68.2, 65.4, 70.4]]
std['svhn'] = [0.1751, 0.1771, 0.1744]
std['stl10'] = [x / 255 for x in [68.4, 66.6, 68.5]]
std['imagenet'] = [0.229, 0.224, 0.225]
std['CelebA'] = [0.229, 0.224, 0.225]
std['FER13'] = [0.229, 0.224, 0.225]
std['KDEF'] = [0.229, 0.224, 0.225]
std['DDCF'] = [0.229, 0.224, 0.225]
std['RAF'] = [0.229, 0.224, 0.225]
std['AffectNet'] = [0.229, 0.224, 0.225]
std['SemiInat'] = [0.2348, 0.2243, 0.2408]


def accimage_loader(path):
    import accimage
    try:
        return accimage.Image(path)
    except IOError:
        # Potentially a decoding problem, fall back to PIL.Image
        return pil_loader(path)


def pil_loader(path):
    # open path as file to avoid ResourceWarning (https://github.com/python-pillow/Pillow/issues/835)
    with open(path, 'rb') as f:
        img = Image.open(f)
        return img.convert('RGB')


def default_loader(path):
    from torchvision import get_image_backend
    if get_image_backend() == 'accimage':
        return accimage_loader(path)
    else:
        return pil_loader(path)


class ImagenetDataset(torchvision.datasets.ImageFolder):
    def __init__(self, root, transform, ulb, num_labels=-1, algo='fixmatch'):
        super().__init__(root, transform)
        self.ulb = ulb
        self.algo = algo
        self.num_labels = num_labels
        is_valid_file = None
        extensions = ('.jpg', '.jpeg', '.png', '.ppm', '.bmp', '.pgm', '.tif', '.tiff', '.webp')
        samples = self.make_dataset_(self.root, self.class_to_idx, extensions, is_valid_file)
        if len(samples) == 0:
            msg = "Found 0 files in subfolders of: {}\n".format(self.root)
            if extensions is not None:
                msg += "Supported extensions are: {}".format(",".join(extensions))
            raise RuntimeError(msg)

        self.loader = default_loader
        self.extensions = extensions

        self.samples = samples
        self.targets = [s[1] for s in samples]

        if self.ulb:
            self.strong_transform = copy.deepcopy(transform)
            self.strong_transform.transforms.insert(0, RandAugment(3, 5))

    def __getitem__(self, idx):
        path, target = self.samples[idx]
        img = self.loader(path)
        if self.transform is not None:
            img_w = self.transform(img)
        if self.target_transform is not None:
            target = self.target_transform(target)
        if not self.ulb:
            return idx, img_w, target
        else:
            if self.algo in ['fixmatch', 'flexmatch', 'uda']:  # 1 weak, 1 strong
                return idx, img_w, self.strong_transform(img)
            elif self.algo in ['pimodel', 'meanteacher', 'mixmatch']:  # 2 weak
                return idx, img_w, self.transform(img)
            elif self.algo in ['ccssl']:  # 1 weak, 2 strong
                return idx, img_w, self.strong_transform(img), self.strong_transform(img)
            elif self.algo in ['pseudolabel', 'vat']:  # 1 weak
                return idx, img_w
            elif self.algo in ['remixmatch']:
                rotate_v_list = [0, 90, 180, 270]
                rotate_v1 = np.random.choice(rotate_v_list, 1).item()
                img_s1 = self.strong_transform(img)
                img_s1_rot = torchvision.transforms.functional.rotate(img_s1, rotate_v1)
                img_s2 = self.strong_transform(img)
                return idx, img_w, img_s1, img_s2, img_s1_rot, rotate_v_list.index(rotate_v1)
            elif self.algo == 'fullysupervised':
                return idx


    def make_dataset_(
            self,
            directory,
            class_to_idx,
            extensions=None,
            is_valid_file=None,
    ):
        instances = []
        directory = os.path.expanduser(directory)
        both_none = extensions is None and is_valid_file is None
        both_something = extensions is not None and is_valid_file is not None
        if both_none or both_something:
            raise ValueError("Both extensions and is_valid_file cannot be None or not None at the same time")
        if extensions is not None:
            def is_valid_file(x: str) -> bool:
                return x.lower().endswith(extensions)

        lb_idx = {}

        for target_class in sorted(class_to_idx.keys()):
            class_index = class_to_idx[target_class]
            target_dir = os.path.join(directory, target_class)
            if not os.path.isdir(target_dir):
                continue
            for root, _, fnames in sorted(os.walk(target_dir, followlinks=True)):
                random.shuffle(fnames)
                if self.num_labels != -1:
                    fnames = fnames[:self.num_labels]
                if self.num_labels != -1:
                    lb_idx[target_class] = fnames
                for fname in fnames:
                    path = os.path.join(root, fname)
                    if is_valid_file(path):
                        item = path, class_index
                        instances.append(item)
        if self.num_labels != -1:
            with open('./sampled_label_idx.json', 'w') as f:
                json.dump(lb_idx, f)
        del lb_idx
        gc.collect()
        return instances


# Image SSL dataloader
class ImageDatasetLoader:
    def __init__(self, root_path, num_labels=-1, crop_size=-1, num_class=1000, dataset='imagenet', algo='fixmatch', args=None):
        self.root_path = os.path.join(root_path)
        self.num_labels = num_labels // num_class
        self.dataset = dataset
        self.algo = algo
        self.args = args
        self.crop_size = crop_size

    def get_transform(self, train, ulb=False):
        crop_size = resize_size = self.crop_size
        if train:
            transform = transforms.Compose([
                transforms.Resize([resize_size, resize_size]),
                transforms.RandomHorizontalFlip(),
                transforms.RandomCrop(crop_size, padding=4, padding_mode='reflect'),
                transforms.ToTensor(),
                transforms.Normalize(mean["imagenet"], std["imagenet"])])
        else:
            transform = transforms.Compose([
                transforms.Resize([crop_size, crop_size]),
                transforms.ToTensor(),
                transforms.Normalize(mean["imagenet"], std["imagenet"])])
        return transform

    def get_lb_train_data(self):
        transform = self.get_transform(train=True, ulb=False)
        data = ImagenetDataset(root=os.path.join(self.root_path, "train"), transform=transform, ulb=False,
                               num_labels=self.num_labels, algo=self.algo)
        count = [0 for _ in range(self.args.num_classes)]
        for c in data.targets:
            count[c] += 1
        dist = np.array(count, dtype=float)
        dist = dist / dist.sum()
        dist = dist.tolist()
        out = {"distribution": dist}
        output_file = r"./data_statistics/"
        output_path = output_file + str(self.dataset) + '_' + str(self.args.num_labels) + '.json'
        if not os.path.exists(output_file):
            os.makedirs(output_file, exist_ok=True)
        with open(output_path, 'w') as w:
            json.dump(out, w)
        return data

    def get_ulb_train_data(self, ulb_folder='train'):
        transform = self.get_transform(train=True, ulb=True)
        data = ImagenetDataset(root=os.path.join(self.root_path, ulb_folder), transform=transform, ulb=True, algo=self.algo)
        return data

    def get_lb_test_data(self):
        transform = self.get_transform(train=False, ulb=False)
        data = ImagenetDataset(root=os.path.join(self.root_path, "val"), transform=transform, ulb=False, algo=self.algo)
        return data


def get_transform(mean, std, crop_size, train=True):
    if train:
        return transforms.Compose([transforms.RandomHorizontalFlip(),
                                   transforms.RandomCrop(crop_size, padding=4, padding_mode='reflect'),
                                   transforms.ToTensor(),
                                   transforms.Normalize(mean, std)])
    else:
        return transforms.Compose([transforms.ToTensor(),
                                   transforms.Normalize(mean, std)])


class SSL_Dataset:
    """
    SSL_Dataset class gets dataset from torchvision.datasets,
    separates labeled and unlabeled data,
    and return BasicDataset: torch.utils.data.Dataset (see datasets.dataset.py)
    """

    def __init__(self,
                 args,
                 alg='fixmatch',
                 name='cifar10',
                 train=True,
                 num_classes=10,
                 crop_size=-1,
                 data_dir='./data',
                 extra_data=None):
        """
        Args
            alg: SSL algorithms
            name: name of dataset in torchvision.datasets (cifar10, cifar100, svhn, stl10)
            train: True means the dataset is training dataset (default=True)
            num_classes: number of label classes
            data_dir: path of directory, where data is downloaed or stored.
        """
        self.args = args
        self.alg = alg
        self.name = name
        self.train = train
        self.num_classes = num_classes
        self.data_dir = data_dir
        crop_size = crop_size
        print('Selected dataset: {}, and crop size: {}'.format(name, crop_size))
        self.transform = get_transform(mean[name], std[name], crop_size, train)
        self.extra_data = extra_data

    def get_data(self, svhn_extra=True):
        """
        get_data returns data (images) and targets (labels)
        shape of data: B, H, W, C
        shape of labels: B,
        """
        import datasets.affect_dataset as dset
        builder = getattr(dset, self.name)
        dataset = builder(root=self.data_dir, train=self.train)
        if self.extra_data is not None:
            return np.append(self.extra_data.data, dataset.data, axis=0), np.append(self.extra_data.targets, dataset.targets, axis=0).tolist()
        return dataset.data, dataset.targets.tolist()

    def get_dset(self, is_ulb=False,
                 strong_transform=None, onehot=False):
        """
        get_dset returns class BasicDataset, containing the returns of get_data.
        
        Args
            is_ulb: If True, returned dataset generates a pair of weak and strong augmented images.
            strong_transform: list of strong_transform (augmentation) if use_strong_transform is True。
            onehot: If True, the label is not integer, but one-hot vector.
        """

        if self.name.upper() == 'STL10':
            data, targets, _ = self.get_data()
        else:
            data, targets = self.get_data()
        num_classes = self.num_classes
        transform = self.transform

        return BasicDataset(self.alg, data, targets, num_classes, transform,
                            is_ulb, strong_transform, onehot)

    def get_ssl_dset(self, num_labels, index=None, include_lb_to_ulb=True,
                     strong_transform=None, onehot=False, use_full_data=False):
        """
        get_ssl_dset split training samples into labeled and unlabeled samples.
        The labeled data is balanced samples over classes.
        
        Args:
            num_labels: number of labeled data.
            index: If index of np.array is given, labeled data is not randomly sampled, but use index for sampling.
            include_lb_to_ulb: If True, consistency regularization is also computed for the labeled data.
            strong_transform: list of strong transform (RandAugment in FixMatch)
            onehot: If True, the target is converted into onehot vector.
            use_full_data: If True, the full dataset is used for supervised traning.
            
        Returns:
            BasicDataset (for labeled data), BasicDataset (for unlabeld data)
        """
        # Supervised top line using all data as labeled data.
        if self.alg == 'fullysupervised' and use_full_data:
            lb_data, lb_targets = self.get_data()
            lb_dset = BasicDataset(self.alg, lb_data, lb_targets, self.num_classes,
                                   self.transform, False, None, onehot)
            return lb_dset, None

        if self.name.upper() == 'STL10':
            lb_data, lb_targets, ulb_data = self.get_data()
            if include_lb_to_ulb:
                ulb_data = np.concatenate([ulb_data, lb_data], axis=0)
            lb_data, lb_targets, _ = sample_labeled_data(self.args, lb_data, lb_targets, num_labels, self.num_classes)
            ulb_targets = None
        else:
            data, targets = self.get_data()
            lb_data, lb_targets, ulb_data, ulb_targets = split_ssl_data(self.args, data, targets,
                                                                        num_labels, self.num_classes,
                                                                        index, include_lb_to_ulb)
        # output the distribution of labeled data for remixmatch
        count = [0 for _ in range(self.num_classes)]
        for c in lb_targets:
            count[c] += 1
        dist = np.array(count, dtype=float)
        dist = dist / dist.sum()
        dist = dist.tolist()
        out = {"distribution": dist}
        output_file = r"./data_statistics/"
        output_path = output_file + str(self.name) + '_' + str(num_labels) + '.json'
        if not os.path.exists(output_file):
            os.makedirs(output_file, exist_ok=True)
        with open(output_path, 'w') as w:
            json.dump(out, w)
        # print(Counter(ulb_targets.tolist()))
        lb_dset = BasicDataset(self.alg, lb_data, lb_targets, self.num_classes,
                               self.transform, False, None, onehot)

        ulb_dset = BasicDataset(self.alg, ulb_data, ulb_targets, self.num_classes,
                                self.transform, True, strong_transform, onehot)
        # print(lb_data.shape)
        # print(ulb_data.shape)
        return lb_dset, ulb_dset
