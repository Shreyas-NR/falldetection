import copy
import logging
import os
import numpy as np
import json
import matplotlib.pyplot as plt
import cv2
import torch.utils.data
import torchvision
import torch.optim.lr_scheduler as lr_scheduler
import logging
import time
import datetime
import random
from PIL import Image
from tqdm import tqdm
from torch.utils.tensorboard import SummaryWriter
import sys
sys.path.insert(0, '..')
# from A2J_experiments import model, anchor, resnet, random_erasing
import model as model
import anchor as anchor
import torch, torch.nn as nn

os.environ["CUDA_VISIBLE_DEVICES"] = "0,1,2,3,4,5"

# DataHyperParms
keypointsNumber = 15
imgWidth = 480
imgHeight = 512
cropWidth = 288
cropHeight = 288
batch_size = 128
learning_rate = 0.00035
Weight_Decay = 1e-4
nepoch = 10000
RegLossFactor = 3
spatialFactor = 0.5
RandCropShift = 5
RandshiftDepth = 0.01
RandRotate = 10
intrinsics = {'fx': 504.1189880371094, 'fy': 504.042724609375, 'cx': 231.7421875, 'cy': 320.62640380859375}

def pixel2world(x, y, z):
    worldX = (x - intrinsics['cx']) * z / intrinsics['fx']
    worldY = (y - intrinsics['cy']) * z / intrinsics['fy']
    return worldX, worldY


#save_dir = './result'

save_dir = './result/MP3DHP_batch_64/'

try:
    os.makedirs(save_dir)
except OSError:
    pass



#directories on the server:
trainingImageDir = '/mnt/beegfs/home/ramesh/Datasets/MP-3DHP/ardata/dataset/train_val/depth_maps/'

testingImageDir = '/mnt/beegfs/home/ramesh/Datasets/MP-3DHP/ardata/dataset/train_val/depth_maps/'

MEAN = 3
STD = 2

annotations_train = dict()

with open('/mnt/beegfs/home/ramesh/Datasets/MP-3DHP/ardata/labels/train_val/labels_train.json') as f:
    dict = json.load(f)
    annotations_train = dict

keypointsUVD_train = []
keypoints2d_train = []
bndbox_train = []
for key,value in dict.items():
    if key != 'intrinsics':
        temp = value[0]
        keypointsUVD_train.append(temp['3d_joints'])
        keypoints2d_train.append(temp['2d_joints'])
        bndbox_train.append(temp['bbox'])

keypointsUVD_train = np.asarray(keypointsUVD_train, dtype=np.float32)
keypoints2d_train = np.asarray(keypoints2d_train, dtype=np.float32)
bndbox_train = np.asarray(bndbox_train, dtype=np.float32)
TrainImgFrames = len(keypoints2d_train)


with open('/mnt/beegfs/home/ramesh/Datasets/MP-3DHP/ardata/labels/train_val/labels_test.json') as f:
    dict = json.load(f)
    annotations_test = dict

keypointsUVD_test = []
keypoints2d_test = []
bndbox_test = []
for key,value in dict.items():
    if key != 'intrinsics':
        temp = value[0]
        keypointsUVD_test.append(temp['3d_joints'])
        keypoints2d_test.append(temp['2d_joints'])
        bndbox_test.append(temp['bbox'])

keypointsUVD_test = np.asarray(keypointsUVD_test, dtype=np.float32)
keypoints2d_test = np.asarray(keypoints2d_test, dtype=np.float32)
bndbox_test = np.asarray(bndbox_test, dtype=np.float32)
TestImgFrames = len(keypoints2d_test)

test_image_ids = list(annotations_test.keys())
train_image_ids = list(annotations_train.keys())

joint_id_to_name = {
  0: 'Head',
  1: 'Neck',
  2: 'RShoulder',
  3: 'LShoulder',
  4: 'RElbow',
  5: 'LElbow',
  6: 'RHand',
  7: 'LHand',
  8: 'Torso',
  9: 'RHip',
  10: 'LHip',
  11: 'RKnee',
  12: 'LKnee',
  13: 'RFoot',
  14: 'LFoot',
}

# use GPU if available   
if (torch.cuda.device_count() > 0):
    print('You have',torch.cuda.device_count(),'CUDA devices available')
    for i in range(torch.cuda.device_count()):
        print(' Device',str(i),': ',torch.cuda.get_device_name(i))
    print('Selecting all available devices')
    device = torch.device('cuda')
else:
    print('No CUDA devices available..selecting CPU')
    device = torch.device('cpu')


def kp_connections(keypoints):
    kp_lines = [
        [keypoints.index('torso'), keypoints.index('right_hip')],
        [keypoints.index('right_hip'), keypoints.index('right_knee')],
        [keypoints.index('right_knee'), keypoints.index('right_ankle')],
        [keypoints.index('torso'), keypoints.index('left_hip')],
        [keypoints.index('left_hip'), keypoints.index('left_knee')],
        [keypoints.index('left_knee'), keypoints.index('left_ankle')],
        [keypoints.index('torso'), keypoints.index('neck')],
        [keypoints.index('neck'), keypoints.index('right_shoulder')],
        [keypoints.index('right_shoulder'), keypoints.index('right_elbow')],
        [keypoints.index('right_elbow'), keypoints.index('right_wrist')],
        [keypoints.index('neck'), keypoints.index('left_shoulder')],
        [keypoints.index('left_shoulder'), keypoints.index('left_elbow')],
        [keypoints.index('left_elbow'), keypoints.index('left_wrist')],
        [keypoints.index('neck'), keypoints.index('head')]
    ]
    return kp_lines

jointColors = [[255, 0, 0], [255, 85, 0], [255, 170, 0], [255, 255, 0], [170, 255, 0], [85, 255, 0], [0, 255, 0],
               [0, 255, 85], [0, 255, 170], [0, 255, 255], [0, 170, 255], [0, 85, 255], [0, 0, 255], [85, 0, 255],
               [170, 0, 255], [255, 0, 255], [255, 0, 170], [255, 0, 85], [85, 255, 85]]

def get_keypoints():
    """Get the itop keypoints"""
    keypoints = [
        'head',
        'neck',
        'right_shoulder',
        'left_shoulder',
        'right_elbow',
        'left_elbow',
        'right_wrist',
        'left_wrist',
        'torso',
        'right_hip',
        'left_hip',
        'right_knee',
        'left_knee',
        'right_ankle',
        'left_ankle']
    return keypoints

def transform(img, label, matrix):
    '''
    img: [H, W]  label, [N,2]
    '''
    img_out = cv2.warpAffine(img,matrix,(cropWidth,cropHeight))
    label_out = np.ones((keypointsNumber, 3))
    label_out[:,:2] = label[:,:2].copy()
    label_out = np.matmul(matrix, label_out.transpose())
    label_out = label_out.transpose()

    return img_out, label_out

def draw_humans_visibility(img, humans, limbs, jointColors, visibilities=None):
    visibilities = visibilities or None
    for i, human in enumerate(humans):
        human_vis = np.array(human)
        for k, limb in enumerate(limbs):
            if visibilities is not None and visibilities[i][limb[0]] < 0.5:
                color = [0, 0, 0]
            else:
                color = [0, 0, 255]
            center1 = human_vis[limb[0], :2].astype(int)
            img = cv2.circle(img, tuple(center1), 3, color, thickness=2, lineType=8, shift=0)

            if visibilities is not None and visibilities[i][limb[1]] < 0.5:
                color = [0, 0, 0]
            else:
                color = [0, 0, 255]
            center2 = human_vis[limb[1], :2].astype(int)
            img = cv2.line(img, tuple(center1), tuple(center2), jointColors[k], 2)
            img = cv2.circle(img, tuple(center2), 3, color, thickness=2, lineType=8, shift=0)

    return img

def evaluation2D_perJoint(source, target, dist_th_2d):
    assert np.shape(source) == np.shape(target), "source has different shape with target"
    count = 0
    acc_vec = []

    for j in range(keypointsNumber):
        for i in range(len(source)):
            if np.square(source[i, j, 0] - target[i, j, 0]) + np.square(
                    source[i, j, 1] - target[i, j, 1]) < np.square(dist_th_2d):
                count = count + 1

        accuracy = count / (len(source))
        print('joint_', j, joint_id_to_name[j], ', accuracy: ', accuracy)
        acc_vec.append(accuracy)
        accuracy = 0
        count = 0


def evaluation2D(source, target, dist_th_2d):
    assert np.shape(source) == np.shape(target), "source has different shape with target"
    count = 0
    for i in range(len(source)):
        for j in range(keypointsNumber):
            if np.square(source[i, j, 0] - target[i, j, 0]) + np.square(
                    source[i, j, 1] - target[i, j, 1]) < np.square(dist_th_2d):
                count = count + 1
    accuracy = count / (len(source) * keypointsNumber)
    return accuracy




def dataPreprocess(index, imgDir, keypointsPixel, keypointsWorld, bndbox, mode, augment = True):
    try:
        if mode == 'TEST':
            depth_img = np.load(os.path.join(imgDir, test_image_ids[index])).astype(float)
        else:
            depth_img = np.load(os.path.join(imgDir, train_image_ids[index])).astype(float)

    except Exception as e:
        print("----------------------------------------------------------------------------------------------------")
        print(f"Caught an exception reading the file: {train_image_ids[index]}")
        print (str(e))
        print("----------------------------------------------------------------------------------------------------")
        raise


    # if mode == 'TEST':
    #     depth_img = np.load(os.path.join(imgDir, test_image_ids[index])).astype(float)
    # else:
    #     depth_img = np.load(os.path.join(imgDir, train_image_ids[index])).astype(float)

    # print(f"train_image_ids[index = {index}] = {train_image_ids[index]}")
    # print(f"Shape of the image[{index}] = {depth_img.shape}")
    imageOutputs = np.ones((cropHeight, cropWidth, 1), dtype='float32')
    labelOutputs = np.ones((keypointsNumber, 3), dtype = 'float32')
    if augment:
        RandomOffset_1 = np.random.randint(-1 * RandCropShift, RandCropShift)
        RandomOffset_2 = np.random.randint(-1 * RandCropShift, RandCropShift)
        RandomOffset_3 = np.random.randint(-1 * RandCropShift, RandCropShift)
        RandomOffset_4 = np.random.randint(-1 * RandCropShift, RandCropShift)
        RandomOffsetDepth = np.random.normal(0, RandshiftDepth, cropHeight * cropWidth).reshape(cropHeight, cropWidth)
        RandomOffsetDepth[np.where(RandomOffsetDepth < RandshiftDepth)] = 0
        RandomRotate = np.random.randint(-1 * RandRotate, RandRotate)
        #RandomScale = np.random.rand() * RandScale[0] + RandScale[1]
        RandomScale = np.random.randint(700, 1700)
        RandomScale /= 1000
        matrix = cv2.getRotationMatrix2D((cropWidth / 2, cropHeight / 2), RandomRotate, RandomScale)
    else:
        RandomOffset_1, RandomOffset_2, RandomOffset_3, RandomOffset_4 = 0, 0, 0, 0
        RandomRotate = 0
        RandomScale = 1
        RandomOffsetDepth = 0
        matrix = cv2.getRotationMatrix2D((cropWidth / 2, cropHeight / 2), RandomRotate, RandomScale)

    new_Xmin = max(bndbox[index][0], 0)
    new_Ymin = max(bndbox[index][1], 0)
    new_Xmax = min(bndbox[index][2], depth_img.shape[1] - 1)
    new_Ymax = min(bndbox[index][3], depth_img.shape[0] - 1)

    imCrop = depth_img.copy()[int(new_Ymin):int(new_Ymax), int(new_Xmin):int(new_Xmax)]
    img_padded = np.zeros((int(bndbox[index][3] - bndbox[index][1]), int(bndbox[index][2] - bndbox[index][0])))

    start1 = 0
    start2 = 0
    end1 = img_padded.shape[0]
    end2 = img_padded.shape[1]
    if bndbox[index][0] < 0 or bndbox[index][1] < 0 or bndbox[index][2] > imgWidth or bndbox[index][3] > imgHeight:
        if bndbox[index][1] < 0:
            start1 = 0 - bndbox[index][1]
        if bndbox[index][0] < 0:
            start2 = 0 - bndbox[index][0]
        if bndbox[index][3] > imgHeight:
            end1 = imCrop.shape[0] + start1
        if bndbox[index][2] > imgWidth:
            end2 = imCrop.shape[1] + start2

        for i in range(img_padded.shape[0]):
            for j in range(img_padded.shape[1]):
                if start1 < i < end1 and start2 < j < end2 and int(i - start1) < imCrop.shape[0] and int(j - start2) < \
                        imCrop.shape[1]:
                    img_padded[i][j] = \
                        imCrop[int(i - start1)][int(j - start2)]
        imCrop = img_padded

    imgResize = cv2.resize(imCrop, (cropWidth, cropHeight), interpolation=cv2.INTER_NEAREST)
    imgResize = np.asarray(imgResize,dtype = 'float32')  # H*W*C
    imgResize = (imgResize - MEAN) / STD

    label_xy = np.ones((keypointsNumber, 2), dtype = 'float32')
    label_xy[:,0] = (keypointsPixel[index,:,0] - new_Xmin + start2)*cropWidth/img_padded.shape[1]
    label_xy[:,1] = (keypointsPixel[index,:,1] - new_Ymin + start1)*cropHeight/img_padded.shape[0]

    if augment:
        imgResize, label_xy = transform(imgResize, label_xy, matrix)

    imageOutputs[:, :, 0] = imgResize

    labelOutputs[:, 1] = label_xy[:, 0]
    labelOutputs[:, 0] = label_xy[:, 1]
    labelOutputs[:, 2] = (keypointsWorld.copy()[index, :, 2]) 

    imageOutputs = np.asarray(imageOutputs)
    imageNCHWOut = imageOutputs.transpose(2, 0, 1)  # [H, W, C] --->>>  [C, H, W]
    imageNCHWOut = np.asarray(imageNCHWOut)
    labelOutputs = np.asarray(labelOutputs)

    data, label = torch.from_numpy(imageNCHWOut), torch.from_numpy(labelOutputs)

    return data, label
######################   Pytorch dataloader   #################
class my_dataloader(torch.utils.data.Dataset):

    def __init__(self, imageDir, bndbox, keypointsPixel, keypointsWorld, mode, augment = True):
        self.imageDir = imageDir
        self.mean = MEAN
        self.std = STD
        self.bndbox = bndbox
        self.keypointsPixel = keypointsPixel
        self.keypointsWorld = keypointsWorld
        self.mode = mode
        self.augment = augment


    def __getitem__(self, index):
        #print(f"index = {index}")
        #if index!=35221:
        data, label = dataPreprocess(index, self.imageDir, self.keypointsPixel, self.keypointsWorld,
                                         self.bndbox, self.mode, self.augment)
            # datab, labelb = data, label
        return data, label
        #else:
         #   print("----------------------------------------------------------------------------------------------------")
          #  print(f"Skipped the file: {train_image_ids[index]} from __getitem__()")
           # print("----------------------------------------------------------------------------------------------------")            # return data, label
    def __len__(self):
        #if index!=35221:
        return len(self.bndbox)


train_image_datasets = my_dataloader(trainingImageDir, bndbox_train, keypoints2d_train, keypointsUVD_train,
                                     mode = 'TRAIN', augment=True)
train_dataloaders = torch.utils.data.DataLoader(train_image_datasets, batch_size = batch_size,
                                             shuffle = False, num_workers = 8)

test_image_datasets = my_dataloader(testingImageDir, bndbox_test, keypoints2d_test, keypointsUVD_test,
                                     mode = 'TEST', augment=False)
test_dataloaders = torch.utils.data.DataLoader(test_image_datasets, batch_size = batch_size,
                                             shuffle = False, num_workers = 8)

writer = SummaryWriter()
print(f"Length of train_dataloaders = ", {len(train_dataloaders)})


def train():
    net = model.A2J_model(num_classes=keypointsNumber)
    # net = net.cuda()
    net = torch.nn.DataParallel(net).to(device)
    x = 0
    post_precess = anchor.post_process(shape=[cropWidth // 16, cropHeight// 16], stride=16, P_h=None, P_w=None)
    criterion = anchor.A2J_loss(shape=[cropWidth// 16, cropHeight // 16], thres=[16.0, 32.0], stride=16, \
                                spatialFactor=spatialFactor, img_shape=[cropWidth, cropHeight], P_h=None, P_w=None)
    optimizer = torch.optim.Adam(net.parameters(), lr=learning_rate, weight_decay=Weight_Decay)
    scheduler = lr_scheduler.StepLR(optimizer, step_size=10, gamma=0.2)

    logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%Y/%m/%d %H:%M:%S', \
                        filename=os.path.join(save_dir, 'train.log'), force=True, level=logging.INFO)
    logging.info('======================================================')

    for epoch in range(nepoch):
        net = net.train()
        train_loss_add = 0.0
        val_loss_add = 0.0
        Cls_loss_add = 0.0
        Cls_loss_val_add = 0.0
        Reg_loss_add = 0.0
        Reg_loss_val_add = 0.0
        updated_best_ckpt = 0.0
        best_ckpt = 0.0
        timer = time.time()
        # Training loop
        for i, (img, label) in enumerate(train_dataloaders):
            x = x+1
            # print(f"x = {x}")
            torch.cuda.synchronize()

            # img, label = img.cuda(), label.cuda()
            img, label = img.to(device), label.to(device)

            heads = net(img)
            optimizer.zero_grad()


            # visualize input images to the network
            # depth_max = 6
            # depth_mean = MEAN
            # depth_std = STD
            # img, label = img.cpu(), label.cpu()
            # img_ = img.numpy().copy()
            # img_ = img_[0, 0, :, :]
            # label = label.numpy()
            # label_new = np.copy(label)
            # label_new[:, :, 0] = label[:, :, 1]
            # label_new[:, :, 1] = label[:, :, 0]

            # human_2d = [label_new[0]]

            # single_img = np.copy(img_)
            # single_img *= depth_std
            # single_img += depth_mean
            # single_img[single_img >= depth_max] = depth_max
            # single_img /= depth_max
            # single_img *= 255
            # single_img = draw_humans_visibility(single_img,
            #                                     human_2d,
            #                                     kp_connections(get_keypoints()),
            #                                     jointColors)
            # plt.imshow(single_img)
            # single_img = single_img.astype(np.uint8)
            # cv2.imwrite(save_dir + '/images/input/input' + str(i) + '.png', single_img)
            # #label = torch.from_numpy(label).cuda()
            # label = torch.from_numpy(label).to(device)


            Cls_loss, Reg_loss = criterion(heads, label)

            loss = 1 * Cls_loss + Reg_loss * RegLossFactor
            loss.backward()
            optimizer.step()

            torch.cuda.synchronize()

            train_loss_add = train_loss_add + (loss.item()) * len(img)
            Cls_loss_add = Cls_loss_add + (Cls_loss.item()) * len(img)
            Reg_loss_add = Reg_loss_add + (Reg_loss.item()) * len(img)

            # printing loss info
            if i % 10 == 0:
                print('epoch: ', epoch, ' step: ', i, 'Cls_loss ', Cls_loss.item(), 'Reg_loss ', Reg_loss.item(),
                      ' total loss ', loss.item())

        scheduler.step()

        # time taken
        torch.cuda.synchronize()
        timer = time.time() - timer
        timer = timer / TrainImgFrames
        print('==> time to learn 1 sample = %f (ms)' % (timer * 1000))

        train_loss_add = train_loss_add / TrainImgFrames
        Cls_loss_add = Cls_loss_add / TrainImgFrames
        Reg_loss_add = Reg_loss_add / TrainImgFrames
        print('mean train_loss_add of 1 sample: %f, #train_indexes = %d' % (train_loss_add, TrainImgFrames))
        print('mean Cls_loss_add of 1 sample: %f, #train_indexes = %d' % (Cls_loss_add, TrainImgFrames))
        print('mean Reg_loss_add of 1 sample: %f, #train_indexes = %d' % (Reg_loss_add, TrainImgFrames))

        if (epoch % 1 == 0):
            net = net.eval()
            output = torch.FloatTensor()
            outputTrain = torch.FloatTensor()



            for i, (img, label) in tqdm(enumerate(test_dataloaders)):
                with torch.no_grad():
                    # img, label = img.cuda(), label.cuda()
                    img, label = img.to(device), label.to(device)
                    heads = net(img)
                    pred_keypoints = post_precess(heads, voting=False)
                    output = torch.cat([output, pred_keypoints.data.cpu()], 0)
                    
                    Cls_loss_val, Reg_loss_val = criterion(heads, label)
                    loss_val = 1 * Cls_loss_val + Reg_loss_val * RegLossFactor
                    val_loss_add = val_loss_add + (loss_val.item()) * len(img)
                    Cls_loss_val_add = Cls_loss_val_add + (Cls_loss_val.item()) * len(img)
                    Reg_loss_val_add = Reg_loss_val_add + (Reg_loss_val.item()) * len(img)

                    # visualize outputs in validation
                    depth_max = 6
                    depth_mean = MEAN
                    depth_std = STD
                    img, label = img.cpu(), label.cpu()
                    img_ = img.numpy().copy()
                    img_ = img_[0, 0, :, :]
                    pred_keypoints = pred_keypoints.cpu().numpy()
                    pred = pred_keypoints[0]
                    pred_new = pred.copy()
                    pred_new[:, 0] = pred[:, 1]
                    pred_new[:, 1] = pred[:, 0]
                    pred_new = [pred_new]

                    single_img = np.copy(img_)
                    single_img *= depth_std
                    single_img += depth_mean
                    single_img[single_img >= depth_max] = depth_max
                    single_img /= depth_max
                    single_img *= 255
                    single_img = draw_humans_visibility(single_img,
                                                        pred_new,
                                                        kp_connections(get_keypoints()),
                                                        jointColors)
                    plt.imshow(single_img)
                    single_img = single_img.astype(np.uint8)
                    cv2.imwrite(save_dir + '/images/output/epoch' + str(epoch) + 'output' + str(i) + '.png', single_img)

            val_loss_add     = val_loss_add / TestImgFrames
            Cls_loss_val_add = Cls_loss_val_add / TestImgFrames
            Reg_loss_val_add = Reg_loss_val_add / TestImgFrames
            print('mean val_loss_add of 1 sample: %f, #test_indexes = %d' % (val_loss_add, TestImgFrames))
            print('mean Cls_loss_val_add of 1 sample: %f, #test_indexes = %d' % (Cls_loss_val_add, TestImgFrames))
            print('mean Reg_loss_val_add of 1 sample: %f, #test_indexes = %d' % (Reg_loss_val_add, TestImgFrames))

            result = output.cpu().data.numpy()
            w_org = imgWidth
            h_org = imgHeight
            dist_th_2d = 0.02 * np.sqrt(w_org ** 2 + h_org ** 2)

            Test1_ = result.copy()
            Test1_[:, :, 0] = result[:, :, 1]
            Test1_[:, :, 1] = result[:, :, 0]
            Test1 = Test1_  # [x, y, z]

            for i in range(len(Test1_)):
                Test1[i, :, 0] = Test1_[i, :, 0] * (bndbox_test[i, 2] - bndbox_test[i, 0]) / cropWidth + bndbox_test[
                    i, 0]  # x
                Test1[i, :, 1] = Test1_[i, :, 1] * (bndbox_test[i, 3] - bndbox_test[i, 1]) / cropHeight + bndbox_test[
                    i, 1]  # y
                Test1[i, :, 2] = Test1_[i, :, 2] 

            Test2d = Test1.copy()[:, :, 0:2]
            accuracy_2d = evaluation2D(Test2d, keypoints2d_test, dist_th_2d)
            print("Accuracy 2d:", accuracy_2d)
            evaluation2D_perJoint(Test2d, keypoints2d_test, dist_th_2d)
            TestWorld = np.ones((len(Test1), keypointsNumber, 3))
            TestWorld_tuple = pixel2world(Test1[:, :, 0], Test1[:, :, 1], Test1[:, :, 2])

            TestWorld[:, :, 0] = TestWorld_tuple[0]
            TestWorld[:, :, 1] = TestWorld_tuple[1]
            TestWorld[:, :, 2] = Test1[:, :, 2]

            Accuracy_test = evaluation10CMRule(TestWorld, keypointsUVD_test, bndbox_test)
            print('Accuracy:', Accuracy_test)
            evaluation10CMRule_perJoint(TestWorld, keypointsUVD_test, bndbox_test)
            saveNamePrefix = '%s/net_%d_wetD_' % (save_dir, epoch) + str(Weight_Decay) + '_depFact_' + str(
                spatialFactor) + '_RegFact_' + str(RegLossFactor) + '_rndShft_' + str(RandCropShift)
            # torch.save(net.state_dict(), saveNamePrefix + '.pth')
            updated_best_ckpt = Accuracy_test > updated_best_ckpt
            best_ckpt = max(best_ckpt, Accuracy_test)
            if updated_best_ckpt == True:             
                torch.save(net.state_dict(), 'best_chkpt.pth')    
            torch.save(net.state_dict(), saveNamePrefix + '.pth')
            print("-----------------Done Saving Model-----------------")
        # log
        print("-----------------Starting to write the Scalar on tensorboard-----------------")
        writer.add_scalar('Loss/train', train_loss_add, epoch)
        writer.add_scalar('Accuracy/test', Accuracy_test, epoch)
        writer.add_scalar('Accuracy_2d/test', accuracy_2d, epoch)
        writer.add_scalars('Loss', {'Training Loss':train_loss_add,
                                   'Validation Loss':val_loss_add}, epoch)
        print("-----------------Completed writing the Scalar on tensorboard-----------------")


        logging.info('Epoch#%d: total loss=%.4f, Cls_loss=%.4f, Reg_loss=%.4f, lr = %.6f, accuacy = %.4f'
                     % (epoch, train_loss_add, Cls_loss_add, Reg_loss_add, scheduler.get_lr()[0], Accuracy_test))

def evaluation10CMRule(source, target, Bndbox):
    assert np.shape(source) == np.shape(target), "source has different shape with target"

    count = 0
    for i in range(len(source)):
        for j in range(keypointsNumber):
            if np.square(source[i, j, 0] - target[i, j, 0]) + np.square(
                    source[i, j, 1] - target[i, j, 1]) + np.square(source[i, j, 2] - target[i, j, 2]) < np.square(
                    0.1):  # 10cm
                count = count + 1
    accuracy = count / (len(source) * keypointsNumber)
    return accuracy

def evaluation10CMRule_perJoint(source, target, Bndbox):
    assert np.shape(source) == np.shape(target), "source has different shape with target"
    count = 0
    accuracy = 0
    for j in range(keypointsNumber):
        for i in range(len(source)):
            if np.square(source[i, j, 0] - target[i, j, 0]) + np.square(
                    source[i, j, 1] - target[i, j, 1]) + np.square(source[i, j, 2] - target[i, j, 2]) < np.square(
                    0.1):  # 10cm
                count = count + 1

        accuracy = count / (len(source))
        print('joint_', j, joint_id_to_name[j], ', accuracy: ', accuracy)
        accuracy = 0
        count = 0


if __name__ == '__main__':
    train()
