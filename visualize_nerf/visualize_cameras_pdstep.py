import open3d as o3d
import json
import numpy as np
import json

import torch
from kornia import create_meshgrid
import numpy as np
import matplotlib.pyplot as plt
import pytransform3d.visualizer as pv
# from datasets.google_scanned_utils import *
# import cv2
# from PIL import Image

def get_ray_directions(H, W, focal):
    """
    Get ray directions for all pixels in camera coordinate.
    Reference: https://www.scratchapixel.com/lessons/3d-basic-rendering/
               ray-tracing-generating-camera-rays/standard-coordinate-systems

    Inputs:
        H, W, focal: image height, width and focal length

    Outputs:
        directions: (H, W, 3), the direction of the rays in camera coordinate
    """
    grid = create_meshgrid(H, W, normalized_coordinates=False)[0]
    i, j = grid.unbind(-1)
    # the direction here is without +0.5 pixel centering as calibration is not so accurate
    # see https://github.com/bmild/nerf/issues/24
    directions = \
        torch.stack([(i-W/2)/focal, -(j-H/2)/focal, -torch.ones_like(i)], -1) # (H, W, 3)
    print("directions", directions.shape)
    return directions


def get_rays(directions, c2w):
    """
    Get ray origin and normalized directions in world coordinate for all pixels in one image.
    Reference: https://www.scratchapixel.com/lessons/3d-basic-rendering/
               ray-tracing-generating-camera-rays/standard-coordinate-systems

    Inputs:
        directions: (H, W, 3) precomputed ray directions in camera coordinate
        c2w: (3, 4) transformation matrix from camera coordinate to world coordinate

    Outputs:
        rays_o: (H*W, 3), the origin of the rays in world coordinate
        rays_d: (H*W, 3), the normalized direction of the rays in world coordinate
    """
    # Rotate ray directions from camera coordinate to the world coordinate
    rays_d = directions @ c2w[:, :3].T # (H, W, 3)
    rays_d /= torch.norm(rays_d, dim=-1, keepdim=True)
    # The origin of all rays is the camera origin in world coordinate
    rays_o = c2w[:, 3].expand(rays_d.shape) # (H, W, 3)

    rays_d = rays_d.view(-1, 3)
    rays_o = rays_o.view(-1, 3)

    return rays_o, rays_d


def get_camera_frustum(img_size, focal, C2W, frustum_length=0.5, color=[0., 1., 0.]):
    W, H = img_size
    hfov = np.rad2deg(np.arctan(W / 2. / focal) * 2.)
    vfov = np.rad2deg(np.arctan(H / 2. / focal) * 2.)
    half_w = frustum_length * np.tan(np.deg2rad(hfov / 2.))
    half_h = frustum_length * np.tan(np.deg2rad(vfov / 2.))

    # build view frustum for camera (I, 0)
    frustum_points = np.array([[0., 0., 0.],                          # frustum origin
                               [-half_w, -half_h, frustum_length],    # top-left image corner
                               [half_w, -half_h, frustum_length],     # top-right image corner
                               [half_w, half_h, frustum_length],      # bottom-right image corner
                               [-half_w, half_h, frustum_length]])    # bottom-left image corner
    frustum_lines = np.array([[0, i] for i in range(1, 5)] + [[i, (i+1)] for i in range(1, 4)] + [[4, 1]])
    frustum_colors = np.tile(np.array(color).reshape((1, 3)), (frustum_lines.shape[0], 1))

    # frustum_colors = np.vstack((np.tile(np.array([[1., 0., 0.]]), (4, 1)),
    #                            np.tile(np.array([[0., 1., 0.]]), (4, 1))))

    # transform view frustum from (I, 0) to (R, t)
    # C2W = np.linalg.inv(W2C)
    frustum_points = np.dot(np.hstack((frustum_points, np.ones_like(frustum_points[:, 0:1]))), C2W.T)
    frustum_points = frustum_points[:, :3] / frustum_points[:, 3:4]

    return frustum_points, frustum_lines, frustum_colors


def frustums2lineset(frustums):
    N = len(frustums)
    merged_points = np.zeros((N*5, 3))      # 5 vertices per frustum
    merged_lines = np.zeros((N*8, 2))       # 8 lines per frustum
    merged_colors = np.zeros((N*8, 3))      # each line gets a color

    for i, (frustum_points, frustum_lines, frustum_colors) in enumerate(frustums):
        merged_points[i*5:(i+1)*5, :] = frustum_points
        merged_lines[i*8:(i+1)*8, :] = frustum_lines + i*5
        merged_colors[i*8:(i+1)*8, :] = frustum_colors

    lineset = o3d.geometry.LineSet()
    lineset.points = o3d.utility.Vector3dVector(merged_points)
    lineset.lines = o3d.utility.Vector2iVector(merged_lines)
    lineset.colors = o3d.utility.Vector3dVector(merged_colors)

    return lineset

def get_rays_mvs(H, W, focal, c2w):
    ys, xs = torch.meshgrid(torch.linspace(0, H - 1, H), torch.linspace(0, W - 1, W))  # pytorch's meshgrid has indexing='ij'
    ys, xs = ys.reshape(-1), xs.reshape(-1)

    dirs = torch.stack([(xs-W/2)/focal, (ys-H/2)/focal, torch.ones_like(xs)], -1) # use 1 instead of -1
    rays_d = dirs @ c2w[:3,:3].t() # dot product, equals to: [c2w.dot(dir) for dir in dirs]
    # Translate camera frame's origin to the world frame. It is the origin of all rays.
    rays_o = c2w[:, 3].expand(rays_d.shape) # (H, W, 3)
    print("rays_o", rays_o.shape, rays_d.shape)
    rays_d = rays_d.view(-1, 3)
    rays_o = rays_o.view(-1, 3)
    return rays_o, rays_d

class NumpyEncoder(json.JSONEncoder):
    """ Special json encoder for numpy types """
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)

def convert_pose(C2W):
    flip_yz = np.eye(4)
    flip_yz[1, 1] = -1
    flip_yz[2, 2] = -1
    C2W = np.matmul(C2W, flip_yz)
    return C2W

def convert_pose_PD_to_NeRF(C2W):

    flip_axes = np.array([[1,0,0,0],
                         [0,0,-1,0],
                         [0,1,0,0],
                         [0,0,0,1]])
    C2W = np.matmul(C2W, flip_axes)
    return C2W

def read_poses(pose_dir, img_files):
    pose_file = os.path.join(pose_dir, 'pose.json')
    with open(pose_file, "r") as read_content:
        data = json.load(read_content) 
    focal = data['focal']
    img_wh = data['img_size']
    asset_pose_ = data["vehicle_pose"]
    asset_pose_inv = np.linalg.inv(asset_pose_)
    all_c2w = []
    for img_file in img_files:
        c2w = np.array(data['transform'][img_file.split('.')[0]])
        all_c2w.append(asset_pose_inv@ convert_pose_PD_to_NeRF(c2w))
        # all_c2w.append(c2w)
    all_c2w = np.array(all_c2w)
    pose_scale_factor = 1. / np.max(np.abs(all_c2w[:, :3, 3]))
    all_c2w[:, :3, 3] *= pose_scale_factor
    return all_c2w, focal, img_wh

def visualize_cameras(colored_camera_dicts, sphere_radius, camera_size=0.1, geometry_file=None, geometry_type='mesh'):
    sphere = o3d.geometry.TriangleMesh.create_sphere(radius=sphere_radius, resolution=10)
    sphere = o3d.geometry.LineSet.create_from_triangle_mesh(sphere)
    sphere.paint_uniform_color((1, 0, 0))

    coord_frame = o3d.geometry.TriangleMesh.create_coordinate_frame(size=0.2, origin=[0., 0., 0.])
    things_to_draw = [sphere, coord_frame]

    # folder = '/home/zubairirshad/pd-api-py/PDStepv3/train/compact_luxury_001_body_black/train'
    # base_dir = os.path.join(folder, 'train')
    # img_files = os.listdir(os.path.join(folder, 'rgb'))
    # img_files.sort()

    # all_c2w, focal, img_size = read_poses(pose_dir = os.path.join(folder, 'pose'), img_files= img_files)

    pose_dict ={}
    for type, camera_dict in colored_camera_dicts.items():
        print("type")
        color, all_c2w = camera_dict
        cnt = 0
        frustums = []
        # focal = 0.5*800/np.tan(0.5*poses_dict['camera_angle_x'])
        focal = 761.18274
        # all_c2w = []
        # print("len(len(camera_dict['frames']))", len(poses_dict['frames']))
        # all_c2w = []
        # for i in range(len(poses_dict['frames'])):
        #     C2W = np.array(poses_dict['frames'][i]['transform_matrix']).reshape((4, 4))
        #     all_c2w.append(C2W)
        all_c2w = np.array(all_c2w)  
        # pose_scale_factor = 1. / np.max(np.abs(all_c2w[:, :3, 3]))
        # all_c2w[:, :3, 3] *= pose_scale_factor
        pose_dict[type] = [color, all_c2w]

    idx = 0
    fig = pv.figure()
    
    refnerf_poses_dict = {}
    for type, camera_dict in pose_dict.items():
        print("type", type)
        if type =='test':
            type = 'val'
        color, poses_dict = camera_dict
        frustums = []
        focal = focal
        # all_c2w = []
        for C2W in all_c2w:
            # C2W = np.array(poses_dict[i].reshape((4, 4)))
            # all_c2w.append(C2W)
            img_size = (640, 480)
            frustums.append(get_camera_frustum(img_size, focal, convert_pose(C2W), frustum_length=0.1, color=color))
        for C2W in all_c2w:
            fig.plot_transform(A2B=convert_pose(C2W), s=0.1, strict_check=False)
        # refnerf_poses_dict[type] = np.array(all_c2w).tolist()

    cameras = frustums2lineset(frustums)
    things_to_draw.append(cameras)

    # for C2W in all_c2w:
    #     print("C2W", C2W.shape)
    #     fig.plot_transform(A2B=C2W, s=0.1,  strict_check=False)

    # refnerf_poses_dict = json.dumps(refnerf_poses_dict, cls=NumpyEncoder)

    # with open('data.json', 'w') as f:
    #     json.dump(data, f)
    # import pickle
    # with open('refnerf_poses.p', 'wb') as fp:
    #     pickle.dump(refnerf_poses_dict, fp, protocol=pickle.HIGHEST_PROTOCOL)

    # for type, camera_dict in colored_camera_dicts.items():
    #     print("type")
    #     color, poses_dict = camera_dict
    #     idx += 1
    #     cnt = 0
    #     frustums = []
    #     focal = 0.5*800/np.tan(0.5*poses_dict['camera_angle_x'])
    #     all_c2w = []
    #     print("len(len(camera_dict['frames']))", len(poses_dict['frames']))
    #     for i in range(len(poses_dict['frames'])):
    #         C2W = np.array(poses_dict['frames'][i]['transform_matrix']).reshape((4, 4))
    #         all_c2w.append(C2W)
    #         img_size = (800, 800)
    #         frustums.append(get_camera_frustum(img_size, focal, C2W, frustum_length=1.0, color=color))
    #         cnt += 1
    #     for C2W in all_c2w:
    #         fig.plot_transform(A2B=C2W, s=0.2, strict_check=False)
    
        

    directions = get_ray_directions(640, 480, focal) # (h, w, 3)
    print("directions", directions.shape)
    # c2w = convert_pose(all_c2w[0])
    c2w = all_c2w[0]
    c2w = torch.FloatTensor(c2w)[:3, :4]
    rays_o, rays_d = get_rays(directions, c2w)

    # rays_o, rays_d = get_rays_mvs(640, 480, focal, c2w)

    rays_o = rays_o.numpy()
    rays_d = rays_d.numpy()
    for j in range(2500):
        start = rays_o[j,:]
        end = rays_o[j,:] + rays_d[j,:]*0.2
        line = np.concatenate((start[None, :],end[None, :]), axis=0)
        fig.plot(line, c=(1.0, 0.5, 0.0))

        start = rays_o[j,:] + rays_d[j,:]*0.2
        end = rays_o[j,:] + rays_d[j,:]*2.0
        line = np.concatenate((start[None, :],end[None, :]), axis=0)
        fig.plot(line, c=(0.0, 1.0, 0.0))

    # if geometry_file is not None:
    #     if geometry_type == 'mesh':
    #         geometry = o3d.io.read_triangle_mesh(geometry_file)
    #         geometry.compute_vertex_normals()
    #     elif geometry_type == 'pointcloud':
    #         geometry = o3d.io.read_point_cloud(geometry_file)
    #     else:
    #         raise Exception('Unknown geometry_type: ', geometry_type)

    #     things_to_draw.append(geometry)

    # o3d.visualization.draw_geometries(things_to_draw)
    for geometry in things_to_draw:
        fig.add_geometry(geometry)
    fig.show()


if __name__ == '__main__':
    import os

    base_dir = './'

    sphere_radius = 1.
    # train_cam_dict = json.load(open('/home/zubairirshad/mvsnerf/data/nerf_synthetic/nerf_synthetic/hotdog/transforms_train.json'))
    # test_cam_dict = json.load(open('/home/zubairirshad/mvsnerf/data/nerf_synthetic/nerf_synthetic/hotdog/transforms_test.json'))
    
    # path_cam_dict = json.load(open(os.path.join(base_dir, 'camera_path/cam_dict_norm.json')))
    npz_file_path = '/home/zubairirshad/Downloads/simpleCube/cameras_transformation.npz'
    camera_size = 0.1

    folder = '/home/zubairirshad/pd-api-py/PDStepv3/train/compact_luxury_001_body_black/train'
    base_dir = os.path.join(folder, 'train')
    img_files = os.listdir(os.path.join(folder, 'rgb'))
    img_files.sort()

    all_c2w, focal, img_size = read_poses(pose_dir = os.path.join(folder, 'pose'), img_files= img_files)

    colored_camera_dicts = {'train': ([0, 1, 0], all_c2w)}
    
    # # geometry_file = os.path.join(base_dir, 'mesh_norm.ply')
    # # geometry_type = 'mesh'
    # data = np.load('/home/zubairirshad/Downloads/simpleCube/cameras_transformation.npz')
    # lst = data.files
    # all_c2w = []
    # for item in lst:
    #     print(item)
    #     print(data[item])
    #     mat = data[item]
    #     w2c = np.zeros((4, 4))
    #     w2c[:3, :4] = mat
    #     w2c[3, 3] = 1
    #     all_c2w.append(w2c)
    #     colored_camera_dicts = {'train': ([0, 1, 0], all_c2w)}

    visualize_cameras(colored_camera_dicts, sphere_radius, 
                      camera_size=camera_size, geometry_file=None, geometry_type=None)