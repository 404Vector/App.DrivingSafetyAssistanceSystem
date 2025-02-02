import numpy as np
import os
from typing import Optional

class CoordinateConverter:
    vel2cam:np.ndarray
    cam2img:np.ndarray
    """
    ## CoordinateConverter
    좌표계 변환을 수행합니다.

    Examples:
        >>> cam2img = np.ndarray(some_asset.cam2img)
        >>> converter = CoordinateConverter(cam2img = cam2img)
    Author : 김형석
    """

    def __init__(self,  vel2cam:Optional[np.ndarray]=None, # type: ignore   
                        cam2img:Optional[np.ndarray]=None, # type: ignore   
                                         ) -> None:      
        if isinstance(vel2cam, np.ndarray):
            self.vel2cam = vel2cam
        if isinstance(cam2img, np.ndarray):
            self.cam2img = cam2img
    
    @property
    def c_u(self) -> float :
        return self.cam2img[0, 2]
    @property
    def c_v(self) -> float :
        return self.cam2img[1, 2]
    @property
    def f_u(self) -> float :
        return self.cam2img[0, 0] 
    @property
    def f_v(self) -> float :
        return self.cam2img[1, 1] 
    @property
    def b_x(self) -> float :
        return (self.cam2img[0, 3] / -self.f_u)
    @property
    def b_y(self) -> float :
        return (self.cam2img[1, 3] / -self.f_u) 

    def __convert_cart_to_homo(self, pts_3d):
        """ 
        Cartesian Coordinate에서 Homogeneous Coordinate로 변환합니다.
        - pts_3d: nx3 points in Cartesian
        - return -> nx4 points in Homogeneous by pending 1
        """
        n = pts_3d.shape[0]
        pts_3d_hom = np.hstack((pts_3d, np.ones((n, 1))))
        return pts_3d_hom
    
    def project_cam_to_image(self, points_3d:np.ndarray) -> np.ndarray:
        """
        Camera Coordinate에서 Image Plane으로 Project 합니다.
        - pts_3d: nx3 points in Cartesian
        - return -> nx4 points
        """
        points_shape = list(points_3d.shape)
        points_shape[-1] = 1 
        points_4 = np.hstack([points_3d, np.ones(points_shape, points_3d.dtype)])#torch.cat([points_3d, points_3d.new_ones(points_shape)], dim=-1)
        point_2d = points_4 @ self.cam2img.T
        point_2d_res = point_2d[..., :2] / point_2d[..., 2:3]
        point_2d_res = (point_2d_res - 1).round()
        return point_2d_res

    def project_velo_to_cam(self, pts_3d_velo) -> np.ndarray:
        """
        Ridar Coordinate에서 Camera Coordinate으로 변환 합니다.
        - pts_3d: nx3 points in Cartesian
        - return -> nx4 points
        """
        pts_3d_velo = self.__convert_cart_to_homo(pts_3d_velo)  # nx4
        return np.dot(pts_3d_velo, np.transpose(self.vel2cam)) # type: ignore 

    def project_velo_to_image(self, pts_3d_velo):
        """
        Ridar Coordinate에서 Image Plane으로 Project 합니다.
        - pts_3d: nx3 points in Cartesian
        - return -> nx4 points
        """
        pts_3d_rect = self.project_velo_to_cam(pts_3d_velo)
        return self.project_cam_to_image(pts_3d_rect)