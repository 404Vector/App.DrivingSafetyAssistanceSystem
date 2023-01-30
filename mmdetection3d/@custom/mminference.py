# Copyright (c) OpenMMLab. All rights reserved.
import argparse
import os
import os.path as osp
from time import sleep

from mmengine.config import Config, DictAction
from mmengine.registry import RUNNERS
from mmengine.runner import Runner
from mmdet3d.utils import register_all_modules, replace_ceph_backend
from mmdet3d.models.detectors import SMOKEMono3D

import torch
import numpy as np
import cv2
import sys
sys.path.append('..')
import json

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='MMDet3D test (and eval) a model')
    parser.add_argument('config', help='test config file path')
    parser.add_argument('checkpoint', help='checkpoint file')
    parser.add_argument(
        '--work-dir',
        help='the directory to save the file containing evaluation metrics')
    parser.add_argument(
        '--ceph', action='store_true', help='Use ceph as data storage backend')
    parser.add_argument(
        '--show', action='store_true', help='show prediction results')
    parser.add_argument(
        '--show-dir',
        help='directory where painted images will be saved. '
        'If specified, it will be automatically saved '
        'to the work_dir/timestamp/show_dir')
    parser.add_argument(
        '--task',
        type=str,
        choices=[
            'mono_det', 'multi-view_det', 'lidar_det', 'lidar_seg',
            'multi-modality_det'
        ],
        help='Determine the visualization method depending on the task.')
    parser.add_argument(
        '--wait-time', type=float, default=2, help='the interval of show (s)')
    parser.add_argument(
        '--cfg-options',
        nargs='+',
        action=DictAction,
        help='override some settings in the used config, the key-value pair '
        'in xxx=yyy format will be merged into config file. If the value to '
        'be overwritten is a list, it should be like key="[a,b]" or key=a,b '
        'It also allows nested list/tuple values, e.g. key="[(a,b),(c,d)]" '
        'Note that the quotation marks are necessary and that no white space '
        'is allowed.')
    parser.add_argument(
        '--launcher',
        choices=['none', 'pytorch', 'slurm', 'mpi'],
        default='none',
        help='job launcher')
    parser.add_argument('--local_rank', type=int, default=0)
    args = parser.parse_args()
    if 'LOCAL_RANK' not in os.environ:
        os.environ['LOCAL_RANK'] = str(args.local_rank)
    return args

def points_cam2img(points_3d:np.ndarray, proj_mat:np.ndarray) -> np.ndarray:
    """Project points in camera coordinates to image coordinates.

    Args:
        points_3d (np.ndarray): Points in shape (N, 3)
        proj_mat (np.ndarray):
            Transformation matrix between coordinates.

    Returns:
        np.ndarray: Points in image coordinates,
            with shape [N, 2].
    """
    points_shape = list(points_3d.shape)
    points_shape[-1] = 1 
    
    points_4 = np.hstack([points_3d, np.ones(points_shape, points_3d.dtype)])#torch.cat([points_3d, points_3d.new_ones(points_shape)], dim=-1)
    point_2d = points_4 @ proj_mat.T
    point_2d_res = point_2d[..., :2] / point_2d[..., 2:3]

    return point_2d_res

def roty(t):
    """ Rotation about the y-axis. """
    c = np.cos(t)
    s = np.sin(t)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])

def draw_projected_box3d(image, qs, color=(0, 255, 0), thickness=2):
    """ Draw 3d bounding box in image
        qs: (8,3) array of vertices for the 3d box in following order:
            1 -------- 0
           /|         /|
          2 -------- 3 .
          | |        | |
          . 5 -------- 4
          |/         |/
          6 -------- 7
    """
    qs = qs.astype(np.int32)
    # cv2.drawContours(image, [[qs[0].tolist(),qs[3].tolist(),qs[7].tolist(),qs[4].tolist()]], -1, (255,0,0))
    for k in range(0, 4):
        # Ref: http://docs.enthought.com/mayavi/mayavi/auto/mlab_helper_functions.html
        i, j = k, (k + 1) % 4
        cv2.line(image, (qs[i, 0], qs[i, 1]), (qs[j, 0], qs[j, 1]), color, thickness)
        i, j = k + 4, (k + 1) % 4 + 4
        cv2.line(image, (qs[i, 0], qs[i, 1]), (qs[j, 0], qs[j, 1]), color, thickness)

        i, j = k, k + 4
        cv2.line(image, (qs[i, 0], qs[i, 1]), (qs[j, 0], qs[j, 1]), color, thickness)
    return image

def render_result(image:np.ndarray, cam2img:list, bboxes:np.ndarray, labels:np.ndarray, scores:np.ndarray) -> np.ndarray:
    # cv Image 변수 새로 선언 
    points=[]
    for idx, (bbox, label, score) in enumerate(zip(bboxes.tolist(), labels.tolist(), scores.tolist())):
        # Each row is (x, y, z, x_size, y_size, z_size, yaw)
        rotation_metrix = roty(bbox[6])
        w = bbox[3]
        h = bbox[4]
        l = bbox[5]
        x_corners = [w / 2, -w / 2, -w / 2, w / 2, w / 2, -w / 2, -w / 2, w / 2]
        y_corners = [-h, -h, -h, -h, 0, 0, 0, 0]
        z_corners = [l / 2, l / 2, -l / 2, -l / 2, l / 2, l / 2, -l / 2, -l / 2]
        corners_3d = np.dot(rotation_metrix, np.vstack([x_corners, y_corners, z_corners])).astype(np.double)

        corners_3d[0, :] = corners_3d[0, :] + bbox[0] # type: ignore
        corners_3d[1, :] = corners_3d[1, :] + bbox[1] # type: ignore
        corners_3d[2, :] = corners_3d[2, :] + bbox[2]  # type: ignore
        uv_origin = points_cam2img(np.transpose(corners_3d), np.array(cam2img))
        corners_2d = (uv_origin - 1).round()
        draw_projected_box3d(image, 
                             corners_2d, 
                             color=(255 - int(200 * (label/3.)), 200+int(55 * score), int(200 * (label/3.))),
                             thickness=1+int(3 * score)
                            ) # type: ignore
        #좌표 변환 포인트 찍기(corners_3d[0, :], corners_3d[2, :])
        points.append(corners_3d[0, :][:2].tolist())
        points.append(corners_3d[2, :][:2].tolist())
    
    print('points:' ,points)
    #print('corners_2d:', corners_2d)
    
    #새로운 이미지 저장                            
    return image, points

def render_map(points, color = (255,0,0)):
    #points : dtype:np,float32
    
    
    
    return map_image

def inspect_pred(pred, idx:int):
    bottom_center = pred.bottom_center[idx].detach().cpu().numpy().tolist()
    bottom_height = pred.bottom_height[idx].detach().cpu().numpy().tolist()
    center = pred.center[idx].detach().cpu().numpy().tolist()
    corners = pred.corners[idx].detach().cpu().numpy().tolist()
    height = pred.height[idx].detach().cpu().numpy().tolist()
    dims = pred.dims[idx].detach().cpu().numpy().tolist()
    gravity_center = pred.gravity_center[idx].detach().cpu().numpy().tolist()
    local_yaw = pred.local_yaw[idx].detach().cpu().numpy().tolist()
    top_height = pred.top_height[idx].detach().cpu().numpy().tolist()
    volume = pred.volume[idx].detach().cpu().numpy().tolist()
    yaw = pred.yaw[idx].detach().cpu().numpy().tolist()
    tensor = pred.tensor[idx].detach().cpu().numpy().tolist()
    data = {
        'tensor':tensor,
        'height':height,
        'yaw':yaw,
        'local_yaw':local_yaw,
        'center':center,
        'gravity_center':gravity_center,
        'bottom_center':bottom_center,
        'bottom_height':bottom_height,
        'top_height':top_height,
        'corners':corners,
        'dims':dims,
        'volume':volume,
    }
    with open("pred.json", "w") as json_file:
        json.dump(data, json_file)
    return 0

def main():
    args = parse_args()

    register_all_modules(init_default_scope=False)
    cfg = Config.fromfile(args.config)

    if args.ceph:
        cfg = replace_ceph_backend(cfg)

    cfg.launcher = args.launcher
    if args.cfg_options is not None:
        cfg.merge_from_dict(args.cfg_options)

    if args.work_dir is not None:
        cfg.work_dir = args.work_dir
    elif cfg.get('work_dir', None) is None:
        cfg.work_dir = osp.join('./work_dirs',
                                osp.splitext(osp.basename(args.config))[0])
    cfg.load_from = args.checkpoint
    if 'runner_type' not in cfg:
        runner = Runner.from_cfg(cfg)
    else:
        runner = RUNNERS.build(cfg)
    runner.load_or_resume()
    model:SMOKEMono3D = runner.model # type: ignore    
    dataloader = runner.test_dataloader
    model.eval()
    all_points={}
    for idx,datas in enumerate(dataloader):
        image = datas['inputs']['img'][0].numpy().transpose((1,2,0)).astype(np.uint8).copy()  # cv2.imread(out.img_path)
        image = cv2.resize(image, (1242,375))
        outs = model.test_step(datas)
        out = outs[0]
        cam2img:list = out.cam2img
        pred = out.pred_instances_3d
        bboxes:np.ndarray = pred.bboxes_3d.tensor.detach().cpu().numpy()
        labels:np.ndarray = pred.labels_3d.detach().cpu().numpy()
        scores:np.ndarray = pred.scores_3d.detach().cpu().numpy()
        result_image, result_point = render_result(image, cam2img, bboxes, labels, scores)
        
        #o = cv2.imwrite(os.path.join('work_dirs/', 'mminference_result.png'), result_image)
        
        sleep(0.2)
        if idx == 3 :
            break
    print(all_points)
    
      


if __name__ == '__main__':
    main()
