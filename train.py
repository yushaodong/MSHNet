r""" MSHNet training (validation) code """
import argparse
from tqdm import tqdm

import torch.optim as optim
import torch.nn as nn
import torch
import os
from model.mshnet import MsimilarityHyperrelationNetwork
from common.logger import Logger, AverageMeter
from common.evaluation import Evaluator
from common import utils
from data.dataset import FSSDataset
def train(epoch, model, dataloader, optimizer, training):
    r""" Train MSHNet """

    # Force randomness during training / freeze randomness during testing
    utils.fix_randseed(None) if training else utils.fix_randseed(0)
    model.train_mode() if training else model.eval()
    average_meter = AverageMeter(dataloader.dataset)

    for idx, batch in tqdm(enumerate(dataloader)):

        # 1. MSHNet forward pass
        batch = utils.to_cuda(batch)
        logit_mask,loss = model(batch['query_img'], batch['support_imgs'], batch['support_masks'],batch['query_mask'])
        pred_mask = logit_mask.argmax(dim=1)

        # 2. Compute loss & update model parameters
        #loss = model.compute_objective(logit_mask, batch['query_mask'])
        if training:
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        # 3. Evaluate prediction
        area_inter, area_union = Evaluator.classify_prediction(pred_mask, batch)
        average_meter.update(area_inter, area_union, batch['class_id'], loss.detach().clone())
        average_meter.write_process(idx, len(dataloader), epoch, write_batch_idx=50)

    # Write evaluation results
    average_meter.write_result('Training' if training else 'Validation', epoch)
    avg_loss = utils.mean(average_meter.loss_buf)
    miou, fb_iou = average_meter.compute_iou()

    return avg_loss, miou, fb_iou


if __name__ == '__main__':
    # Arguments parsing
    parser = argparse.ArgumentParser(description='MSHNet Pytorch Implementat1ion')
    parser.add_argument('--datapath', type=str, default='C:/dataset/pascal5i/VOCdevkit/')
    #parser.add_argument('--datapath', type=str, default='/home/alex/pytorch/data')
    parser.add_argument('--save_path', type=str, default='./resume')
    parser.add_argument('--benchmark', type=str, default='pascal', choices=['pascal', 'coco', 'fss'])
    parser.add_argument('--logpath', type=str, default='')
    parser.add_argument('--bsz', type=int, default=16)
    parser.add_argument('--shot', type=int, default=1)
    parser.add_argument('--momentum', type=float, default=0.9)
    parser.add_argument('--weight_decay', type=float, default=0.00005)
    parser.add_argument('--lr', type=float, default=0.025)
    parser.add_argument('--niter', type=int, default=300)
    parser.add_argument('--nworker', type=int, default=4)
    parser.add_argument('--fold', type=int, default=2, choices=[0, 1, 2, 3])
    parser.add_argument('--backbone', type=str, default='resnet50', choices=['vgg16', 'resnet50', 'resnet101'])
    args = parser.parse_args()
    Logger.initialize(args, training=True)

    # Model initialization
    model = MsimilarityHyperrelationNetwork(args.backbone, False,shot=args.shot)
    Logger.log_params(model)

    # Device setup
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    Logger.info('# available GPUs: %d' % torch.cuda.device_count())
    #model = nn.DataParallel(model)
    model=model.cuda()
    # Helper classes (for training) initialization
    optimizer = torch.optim.SGD(
        model.merge.parameters(),
        lr=args.lr, momentum=args.momentum, weight_decay=args.weight_decay)
    lrschem = optim.lr_scheduler.ExponentialLR(optimizer, 0.9)
    Evaluator.initialize()
    if os.path.exists(args.save_path + '/resume.pth'):
        print('---load state_dict from: ',args.save_path + '/resume.pth')
        val=torch.load(args.save_path + '/resume.pth')
        epoch=val['epoch']
        model.load_state_dict(val['state_dict'])
        optimizer.load_state_dict(val['optimizer'])
        lrschem.load_state_dict(val['lr'])
    elif os.path.exists(args.save_path + '/weight.pth'):
        model.load_state_dict(torch.load(args.save_path + '/weight.pth'))
        epoch=0
    else:
        epoch=0
        print('---there is no resume or weight: ')
    # Dataset initialization
    print('epoch:',epoch,'lr:',lrschem.get_lr())
    FSSDataset.initialize(img_size=473, datapath=args.datapath, use_original_imgsize=False)
    dataloader_trn = FSSDataset.build_dataloader(args.benchmark, args.bsz, args.nworker, args.fold, 'trn',shot=args.shot)
    dataloader_val = FSSDataset.build_dataloader(args.benchmark, args.bsz, args.nworker, args.fold, 'val',shot=args.shot)

    # Train MSHNet
    best_val_miou = float('-inf')
    best_val_loss = float('inf')
    while epoch < args.niter:
        trn_loss, trn_miou, trn_fb_iou = train(epoch, model, dataloader_trn, optimizer, training=True)
        filename = args.save_path + '/resume.pth'
        torch.save({'epoch': epoch, 'state_dict': model.state_dict(), 'optimizer': optimizer.state_dict(),'lr':lrschem.state_dict()}, filename)
        if epoch%5==0 and epoch!=0:
            lrschem.step()
            print("current lr:",lrschem.get_lr())
        with torch.no_grad():
            val_loss, val_miou, val_fb_iou = train(epoch, model, dataloader_val, optimizer, training=False)

        # Save the best model
        if val_miou > best_val_miou:
            best_val_miou = val_miou
            Logger.save_model_miou(model, epoch, val_miou)
            filename = args.save_path + '/train_epoch_' + str(epoch) + '_' + str(best_val_miou.item()) + '.pth'
            torch.save(model.state_dict(),filename)
        Logger.tbd_writer.add_scalars('data/loss', {'trn_loss': trn_loss, 'val_loss': val_loss}, epoch)
        Logger.tbd_writer.add_scalars('data/miou', {'trn_miou': trn_miou, 'val_miou': val_miou}, epoch)
        Logger.tbd_writer.add_scalars('data/fb_iou', {'trn_fb_iou': trn_fb_iou, 'val_fb_iou': val_fb_iou}, epoch)
        Logger.tbd_writer.flush()
        epoch+=1
    Logger.tbd_writer.close()
    Logger.info('==================== Finished Training ====================')
