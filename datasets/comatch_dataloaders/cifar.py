import os.path
import os.path as osp
import pickle
import numpy as np

import torch
from torch.utils.data import Dataset
from torchvision import transforms

from datasets.comatch_dataloaders import transform as T
from datasets.comatch_dataloaders.randaugment import RandomAugment
from datasets.comatch_dataloaders.sampler import RandomSampler, BatchSampler
from datasets.ssl_dataset import SSL_Dataset, ImageDatasetLoader


class TwoCropsTransform:
    """Take 2 random augmentations of one image."""

    def __init__(self, trans_weak, trans_strong):
        self.trans_weak = trans_weak
        self.trans_strong = trans_strong

    def __call__(self, x):
        x1 = self.trans_weak(x)
        x2 = self.trans_strong(x)
        return [x1, x2]


class ThreeCropsTransform:
    """Take 3 random augmentations of one image."""

    def __init__(self, trans_weak, trans_strong0, trans_strong1):
        self.trans_weak = trans_weak
        self.trans_strong0 = trans_strong0
        self.trans_strong1 = trans_strong1

    def __call__(self, x):
        x1 = self.trans_weak(x)
        x2 = self.trans_strong0(x)
        x3 = self.trans_strong1(x)
        return [x1, x2, x3]


def load_data_train(L=250, dataset='CIFAR10', dspth='./data'):
    if dataset == 'CIFAR10':
        datalist = [
            osp.join(dspth, 'cifar-10-batches-py', 'data_batch_{}'.format(i + 1))
            for i in range(5)
        ]
        n_class = 10
        assert L in [10, 20, 40, 80, 250, 4000]
    elif dataset == 'CIFAR100':
        datalist = [
            osp.join(dspth, 'cifar-100-python', 'train')]
        n_class = 100
        assert L in [25, 400, 2500, 10000]

    data, labels = [], []
    for data_batch in datalist:
        with open(data_batch, 'rb') as fr:
            entry = pickle.load(fr, encoding='latin1')
            lbs = entry['labels'] if 'labels' in entry.keys() else entry['fine_labels']
            data.append(entry['data'])
            labels.append(lbs)
    data = np.concatenate(data, axis=0)
    labels = np.concatenate(labels, axis=0)
    n_labels = L // n_class
    data_x, label_x, data_u, label_u = [], [], [], []
    for i in range(n_class):
        indices = np.where(labels == i)[0]
        np.random.shuffle(indices)
        inds_x, inds_u = indices[:n_labels], indices[n_labels:]
        data_x += [
            data[i].reshape(3, 32, 32).transpose(1, 2, 0)
            for i in inds_x
        ]
        label_x += [labels[i] for i in inds_x]
        data_u += [
            data[i].reshape(3, 32, 32).transpose(1, 2, 0)
            for i in inds_u
        ]
        label_u += [labels[i] for i in inds_u]
    return data_x, label_x, data_u, label_u


def load_data_val(dataset, dspth='./data'):
    if dataset == 'CIFAR10':
        datalist = [
            osp.join(dspth, 'cifar-10-batches-py', 'test_batch')
        ]
    elif dataset == 'CIFAR100':
        datalist = [
            osp.join(dspth, 'cifar-100-python', 'test')
        ]

    data, labels = [], []
    for data_batch in datalist:
        with open(data_batch, 'rb') as fr:
            entry = pickle.load(fr, encoding='latin1')
            lbs = entry['labels'] if 'labels' in entry.keys() else entry['fine_labels']
            data.append(entry['data'])
            labels.append(lbs)
    data = np.concatenate(data, axis=0)
    labels = np.concatenate(labels, axis=0)
    data = [
        el.reshape(3, 32, 32).transpose(1, 2, 0)
        for el in data
    ]
    return data, labels


def compute_mean_var():
    data_x, label_x, data_u, label_u = load_data_train()
    data = data_x + data_u
    data = np.concatenate([el[None, ...] for el in data], axis=0)

    mean, var = [], []
    for i in range(3):
        channel = (data[:, :, :, i].ravel() / 127.5) - 1
        #  channel = (data[:, :, :, i].ravel() / 255)
        mean.append(np.mean(channel))
        var.append(np.std(channel))

    print('mean: ', mean)
    print('var: ', var)


class Cifar(Dataset):
    def __init__(self, dataset, data, labels, mode):
        super(Cifar, self).__init__()
        self.data, self.labels = data, labels
        self.mode = mode
        crop_size = 96 if dataset.upper() == 'STL10' else 48 if dataset.upper() == 'FER13' else 32
        assert len(self.data) == len(self.labels)
        if dataset.upper() == 'CIFAR10':
            mean, std = (0.4914, 0.4822, 0.4465), (0.2471, 0.2435, 0.2616)
        elif dataset.upper() == 'CIFAR100':
            mean, std = (0.5071, 0.4867, 0.4408), (0.2675, 0.2565, 0.2761)
        elif dataset.upper() == 'SVHN':
            mean, std = [0.4380, 0.4440, 0.4730], [0.1751, 0.1771, 0.1744]
        elif dataset.upper() == 'STL10':
            mean, std = [x / 255 for x in [112.4, 109.1, 98.6]], [x / 255 for x in [68.4, 66.6, 68.5]]
        else:
            mean, std = (0.4914, 0.4822, 0.4465), (0.2471, 0.2435, 0.2616)
        trans_weak = T.Compose([
            T.Resize((crop_size, crop_size)),
            T.PadandRandomCrop(border=4, cropsize=(crop_size, crop_size)),
            T.RandomHorizontalFlip(p=0.5),
            T.Normalize(mean, std),
            T.ToTensor(),
        ])
        trans_strong0 = T.Compose([
            T.Resize((crop_size, crop_size)),
            T.PadandRandomCrop(border=4, cropsize=(crop_size, crop_size)),
            T.RandomHorizontalFlip(p=0.5),
            RandomAugment(2, 10),
            T.Normalize(mean, std),
            T.ToTensor(),
        ])
        trans_strong1 = transforms.Compose([
            transforms.ToPILImage(),
            transforms.RandomResizedCrop(crop_size, scale=(0.2, 1.)),
            transforms.RandomHorizontalFlip(p=0.5),
            transforms.RandomApply([
                transforms.ColorJitter(0.4, 0.4, 0.4, 0.1)
            ], p=0.8),
            transforms.RandomGrayscale(p=0.2),
            transforms.ToTensor(),
            transforms.Normalize(mean, std),
        ])
        if self.mode == 'train_x':
            self.trans = trans_weak
        elif self.mode == 'train_u_comatch':
            self.trans = ThreeCropsTransform(trans_weak, trans_strong0, trans_strong1)
        elif self.mode == 'train_u_fixmatch':
            self.trans = TwoCropsTransform(trans_weak, trans_strong0)
        else:
            self.trans = T.Compose([
                T.Resize((crop_size, crop_size)),
                T.Normalize(mean, std),
                T.ToTensor(),
            ])

    def __getitem__(self, idx):
        im, lb = self.data[idx], self.labels[idx]
        return self.trans(im), lb.astype(np.int64)

    def __len__(self):
        leng = len(self.data)
        return leng


def get_train_loader(dataset, batch_size, mu, n_iters_per_epoch, L, root='data', method='comatch', workers=(2, 16), args=None):
    # data_x, label_x, data_u, label_u = load_data_train(L=L, dataset=dataset, dspth=root)
    train_dset = SSL_Dataset(args, alg='fixmatch', name=args.dataset, train=True,
                             num_classes=args.num_classes, data_dir=args.data_dir)
    lb_dset, ulb_dset = train_dset.get_ssl_dset(args.num_labels)

    ds_x = Cifar(
        dataset=dataset,
        data=lb_dset.data,
        labels=lb_dset.targets,
        mode='train_x'
    )  # return an iter of num_samples length (all indices of samples)
    sampler_x = RandomSampler(ds_x, replacement=True, num_samples=n_iters_per_epoch * batch_size)
    batch_sampler_x = BatchSampler(sampler_x, batch_size, drop_last=True)  # yield a batch of samples one time
    dl_x = torch.utils.data.DataLoader(
        ds_x,
        batch_sampler=batch_sampler_x,
        num_workers=workers[0],
        pin_memory=True
    )

    if 'ubl_dataset' in args:
        if os.path.exists('/Users/shuvenduroy/Documents/dataset'):  # local debugging
            args.ubl_data_dir = '/Users/shuvenduroy/Documents/dataset/imagenet100'
        print('loading unlabeled dataset ...', args.ubl_dataset)
        if args.ubl_dataset in ["imagenet", "FER13", 'KDEF', 'AffectNet', "AffectNetAllFace", 'RAF']:
            print('Loading folder dataset ...')
            image_loader = ImageDatasetLoader(root_path=args.ubl_data_dir, num_labels=args.num_labels,
                                              dataset=args.dataset,  # need the image size of original dataset
                                              num_class=args.num_classes, algo=args.alg)
            ulb_dset = image_loader.get_ulb_train_data()
        else:  # stl-10 has different size which is not supported yet
            print('Loading PyTroch dataset ...')
            train_dset = SSL_Dataset(args, alg=args.alg, name=args.ubl_dataset, train=True, num_classes=100, crop_size=96 if args.dataset.upper() == 'STL10' else -1, data_dir=args.ubl_data_dir)
            lb_dset, ulb_dset = train_dset.get_ssl_dset(100)

    ds_u = Cifar(
        dataset=dataset,
        data=ulb_dset.data,
        labels=ulb_dset.targets,
        mode='train_u_%s' % method
    )
    sampler_u = RandomSampler(ds_u, replacement=True, num_samples=mu * n_iters_per_epoch * batch_size)
    # sampler_u = RandomSampler(ds_u, replacement=False)
    batch_sampler_u = BatchSampler(sampler_u, batch_size * mu, drop_last=True)
    dl_u = torch.utils.data.DataLoader(
        ds_u,
        batch_sampler=batch_sampler_u,
        num_workers=workers[1],
        pin_memory=True
    )
    return dl_x, dl_u


def get_val_loader(dataset, batch_size, num_workers, pin_memory=True, root='data', args=None):
    _eval_dset = SSL_Dataset(args, alg='fixmatch', name=args.dataset, train=False,
                             num_classes=args.num_classes, data_dir=args.data_dir)
    eval_dset = _eval_dset.get_dset()

    ds = Cifar(
        dataset=dataset,
        data=eval_dset.data,
        labels=np.array(eval_dset.targets),
        mode='test'
    )
    dl = torch.utils.data.DataLoader(
        ds,
        shuffle=False,
        batch_size=batch_size,
        drop_last=False,
        num_workers=num_workers,
        pin_memory=pin_memory
    )
    return dl
