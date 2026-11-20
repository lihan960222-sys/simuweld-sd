from dataclasses import dataclass

import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.sparse import coo_matrix, csr_matrix


_CORNERS = np.array([[-1., -1.], [1., -1.], [1., 1.], [-1., 1.]])
_INPLANE_POINTS = _CORNERS / np.sqrt(3)
_THICKNESS_POINTS, _THICKNESS_WEIGHTS = leggauss(5)
_MEMBRANE_DOFS = (5 * np.arange(4)[:, None] + np.array([0, 1, 2])).ravel()
_BENDING_DOFS = (5 * np.arange(4)[:, None] + np.array([3, 4])).ravel()
_SHEAR_DOFS = (5 * np.arange(4)[:, None] + np.array([2, 3, 4])).ravel()


def _shape(xi, eta):
    a, b = _CORNERS.T
    shape = (1 + a * xi) * (1 + b * eta) / 4
    derivative = np.array([a * (1 + b * eta), b * (1 + a * xi)]) / 4
    return shape, derivative


@dataclass(frozen=True)
class ShellKinematics:
    strain: np.ndarray
    strain_B: np.ndarray
    shear: np.ndarray


@dataclass(frozen=True)
class ShellResponse:
    energy: float | None
    residual: np.ndarray
    tangent: csr_matrix | None


@dataclass(frozen=True)
class ElasticSection:
    """单位实际面积的 A/B/D、固有应变 N/M、能量及 integral(G dz)，z 相对板参考平面。"""

    stiffness: np.ndarray
    eigen_resultants: np.ndarray
    eigen_energy: np.ndarray
    shear_integral: np.ndarray

    def __post_init__(self):
        for name in ("stiffness", "eigen_resultants", "eigen_energy", "shear_integral"):
            values = np.asarray(getattr(self, name), dtype=float)
            if not np.isfinite(values).all():
                raise ValueError(f"ElasticSection.{name} 包含非有限数值")
            object.__setattr__(self, name, values)
        if self.stiffness.ndim != 4 or self.stiffness.shape[1:] != (3, 3, 3):
            raise ValueError("stiffness 要求 (nel, 3, 3, 3)，第二维依次为 A、B、D")
        count = len(self.stiffness)
        if (self.eigen_resultants.shape != (count, 2, 3) or self.eigen_energy.shape != (count,)
                or self.shear_integral.shape != (count,)):
            raise ValueError("eigen_resultants/eigen_energy/shear_integral 要求 (nel,2,3)/(nel,)/(nel,)")
        if np.any(self.eigen_energy < 0) or np.any(self.shear_integral <= 0):
            raise ValueError("eigen_energy 必须非负，shear_integral 必须为正值")
        if not np.allclose(self.stiffness, self.stiffness.swapaxes(-1, -2), rtol=1e-12, atol=0.):
            raise ValueError("A、B、D 必须为对称矩阵")


class MITC4Shell:
    """平面四节点 shell；SI 单位，节点顺序沿 +z 观察为逆时针。"""

    def __init__(self, nodal_xy, connectivity, thickness, young_modulus, poisson, *, area_fraction=1., section_offset=0.):
        self.nodal_xy = np.array(nodal_xy, dtype=float, copy=True)
        connectivity = np.asarray(connectivity)
        if self.nodal_xy.ndim != 2 or self.nodal_xy.shape[1] != 2 or not np.isfinite(self.nodal_xy).all():
            raise ValueError("nodal_xy 必须为有限数值的 (nnode, 2) 数组")
        if (connectivity.ndim != 2 or connectivity.shape[1] != 4 or len(connectivity) == 0
                or not np.issubdtype(connectivity.dtype, np.integer)):
            raise ValueError("connectivity 必须为非空整数 (nel, 4) 数组")
        if np.any(connectivity < 0) or np.any(connectivity >= len(self.nodal_xy)):
            raise ValueError("connectivity 包含无效节点编号")
        if np.any(np.diff(np.sort(connectivity, axis=1), axis=1) == 0):
            raise ValueError("每个 shell 必须包含四个不同节点")
        self.connectivity = connectivity.astype(np.int64, copy=True)
        self.ndof = 5 * len(self.nodal_xy)
        count = len(connectivity)
        self.thickness = self._element_values(thickness, "thickness")
        self.area_fraction = self._element_values(area_fraction, "area_fraction")
        self.section_offset = self._element_values(section_offset, "section_offset")
        young = self._element_values(young_modulus, "young_modulus")
        poisson = self._element_values(poisson, "poisson")
        if np.any(self.thickness <= 0) or np.any(young <= 0) or np.any((poisson <= -1) | (poisson >= 0.5)):
            raise ValueError("thickness、young_modulus 必须为正值，poisson 必须在 (-1, 0.5) 范围")
        if np.any((self.area_fraction <= 0) | (self.area_fraction > 1)):
            raise ValueError("area_fraction 必须在 (0, 1] 范围")
        self.elasticity = np.zeros((count, 3, 3))
        self.elasticity[:, 0, 0] = self.elasticity[:, 1, 1] = 1
        self.elasticity[:, 0, 1] = self.elasticity[:, 1, 0] = poisson
        self.elasticity[:, 2, 2] = (1 - poisson) / 2
        self.elasticity *= (young / (1 - poisson ** 2))[:, None, None]
        self.shear_modulus = young / (2 * (1 + poisson))
        self.element_dofs = (5 * self.connectivity[:, :, None] + np.arange(5)).reshape(count, 20)
        self.thickness_coordinates = self.section_offset[:, None] + self.thickness[:, None] * _THICKNESS_POINTS / 2
        self.inplane_points = _INPLANE_POINTS.copy()
        self.gradients = np.empty((count, 4, 2, 4))
        self.membrane_B = np.zeros((count, 4, 3, 20))
        self.bending_B = np.zeros_like(self.membrane_B)
        self.shear_B = np.zeros((count, 4, 2, 20))
        self.area_weights = np.empty((count, 4))
        coordinates = self.nodal_xy[self.connectivity]
        # 四个角点验证 Jacobian，确保整个双线性单元的方向有效。
        for point in _CORNERS:
            self._jacobian(coordinates, _shape(*point)[1])
        tying = np.zeros((count, 4, 20))
        for index, (xi, eta, component) in enumerate(((0., -1., 0), (0., 1., 0), (-1., 0., 1), (1., 0., 1))):
            shape, derivative = _shape(xi, eta)
            jacobian, _ = self._jacobian(coordinates, derivative)
            tying[:, index, 2::5] = derivative[component]
            tying[:, index, 3::5] = -jacobian[:, component, 1, None] * shape
            tying[:, index, 4::5] = jacobian[:, component, 0, None] * shape
        for index, (xi, eta) in enumerate(_INPLANE_POINTS):
            _, derivative = _shape(xi, eta)
            jacobian, determinant = self._jacobian(coordinates, derivative)
            inverse = np.linalg.inv(jacobian)
            gradients = np.einsum("eij,jn->ein", inverse, derivative)
            dx, dy = gradients[:, 0], gradients[:, 1]
            self.gradients[:, index] = gradients
            self.area_weights[:, index] = determinant * self.area_fraction
            self.membrane_B[:, index, 0, 0::5] = dx
            self.membrane_B[:, index, 1, 1::5] = dy
            self.membrane_B[:, index, 2, 0::5] = dy
            self.membrane_B[:, index, 2, 1::5] = dx
            # 物理转角：u(z)=u+z*theta_y，v(z)=v-z*theta_x；第三分量为工程 gamma_xy。
            self.bending_B[:, index, 0, 4::5] = dx
            self.bending_B[:, index, 1, 3::5] = -dy
            self.bending_B[:, index, 2, 4::5] = dy
            self.bending_B[:, index, 2, 3::5] = -dx
            covariant = np.array([
                ((1 - eta) * tying[:, 0] + (1 + eta) * tying[:, 1]) / 2,
                ((1 - xi) * tying[:, 2] + (1 + xi) * tying[:, 3]) / 2,
            ]).transpose(1, 0, 2)
            self.shear_B[:, index] = inverse @ covariant
        self.volume_weights = self.area_weights[:, :, None] * self.thickness[:, None, None] * _THICKNESS_WEIGHTS / 2
        self._section_moments = np.array([
            np.sum(self.volume_weights * self.thickness_coordinates[:, None, :] ** power, axis=2)
            for power in range(3)
        ]).transpose(1, 2, 0)
        self._membrane_B = self.membrane_B[:, :, :, _MEMBRANE_DOFS].copy()
        self._bending_B = self.bending_B[:, :, :, _BENDING_DOFS].copy()
        self._shear_B = self.shear_B[:, :, :, _SHEAR_DOFS].copy()
        self._rows = np.broadcast_to(self.element_dofs[:, :, None], (count, 20, 20)).ravel()
        self._columns = np.broadcast_to(self.element_dofs[:, None, :], (count, 20, 20)).ravel()

    def _element_values(self, values, name):
        values = np.asarray(values, dtype=float)
        if values.ndim != 0 and values.shape != (len(self.connectivity),):
            raise ValueError(f"{name} 要求标量或者 (nel,) 数组")
        if not np.isfinite(values).all():
            raise ValueError(f"{name} 包含非有限数值")
        return np.broadcast_to(values, (len(self.connectivity),)).copy()

    @staticmethod
    def _jacobian(coordinates, derivative):
        jacobian = np.einsum("in,enx->eix", derivative, coordinates)
        determinant = np.linalg.det(jacobian)
        scale = np.max(np.abs(jacobian), axis=(1, 2)) ** 2
        if np.any(determinant <= 32 * np.finfo(float).eps * scale):
            raise ValueError("shell 的 Jacobian 非正值或者单元退化")
        return jacobian, determinant

    def _section_kinematics(self, q, derivative):
        q = np.asarray(q, dtype=float)
        if q.shape != (len(self.nodal_xy), 5) or not np.isfinite(q).all():
            raise ValueError("q 必须为有限数值的 (nnode, 5) 数组")
        local = q[self.connectivity].reshape(-1, 20)
        slopes = np.einsum("epin,en->epi", self.gradients, q[self.connectivity, 2])
        wx, wy = slopes[:, :, 0], slopes[:, :, 1]
        membrane = np.einsum("epij,ej->epi", self.membrane_B, local)
        membrane[:, :, 0] += 0.5 * wx ** 2
        membrane[:, :, 1] += 0.5 * wy ** 2
        membrane[:, :, 2] += wx * wy
        curvature = np.einsum("epij,ej->epi", self.bending_B, local)
        shear = np.einsum("epij,ej->epi", self.shear_B, local)
        membrane_B = None
        if derivative:
            membrane_B = self._membrane_B.copy()
            dx, dy = self.gradients[:, :, 0], self.gradients[:, :, 1]
            membrane_B[:, :, 0, 2::3] = wx[:, :, None] * dx
            membrane_B[:, :, 1, 2::3] = wy[:, :, None] * dy
            membrane_B[:, :, 2, 2::3] = wy[:, :, None] * dx + wx[:, :, None] * dy
        return membrane, curvature, shear, membrane_B

    def kinematics(self, q):
        """q=(nnode,5)，顺序 u,v,w,theta_x,theta_y；strain=(nel,4,5,3)。"""
        membrane, curvature, shear, membrane_B = self._section_kinematics(q, True)
        strain = membrane[:, :, None, :] + self.thickness_coordinates[:, None, :, None] * curvature[:, :, None, :]
        full_B = self.membrane_B.copy()
        full_B[:, :, :, 2::5] = membrane_B[:, :, :, 2::3]
        strain_B = full_B[:, :, None] + self.thickness_coordinates[:, None, :, None, None] * self.bending_B[:, :, None]
        return ShellKinematics(strain, strain_B, shear)

    def _shear_weights(self, shear_modulus):
        if shear_modulus is None:
            shear_modulus = self.shear_modulus[:, None, None]
        shear_modulus = np.asarray(shear_modulus, dtype=float)
        if not np.isfinite(shear_modulus).all() or np.any(shear_modulus <= 0):
            raise ValueError("shear_modulus 必须为有限正值")
        return (5 / 6) * np.sum(self.volume_weights * np.broadcast_to(shear_modulus, self.volume_weights.shape), axis=2)

    def assemble(self, kinematics, stress, material_tangent=None, *, shear_modulus=None, tangent=True):
        """积分材料 stress/C；shape=(nel,4,5,3)/(nel,4,5,3,3)，residual 为一维全局数组。"""
        count = len(self.connectivity)
        stress = np.asarray(stress, dtype=float)
        if stress.shape != (count, 4, 5, 3) or not np.isfinite(stress).all():
            raise ValueError("stress 必须为有限数值的 (nel, 4, 5, 3) 数组")
        if (kinematics.strain.shape != stress.shape or kinematics.strain_B.shape != (count, 4, 5, 3, 20)
                or kinematics.shear.shape != (count, 4, 2)):
            raise ValueError("kinematics 与 shell 积分点尺寸不一致")
        weighted_stress = stress * self.volume_weights[:, :, :, None]
        shear_weights = self._shear_weights(shear_modulus)
        local_residual = np.einsum("eptia,epti->ea", kinematics.strain_B, weighted_stress)
        local_residual += np.einsum("epia,epi,ep->ea", self.shear_B, kinematics.shear, shear_weights)
        residual = np.bincount(self.element_dofs.ravel(), weights=local_residual.ravel(), minlength=self.ndof)
        matrix = None
        if tangent:
            material_tangent = np.asarray(material_tangent, dtype=float)
            if material_tangent.shape != (count, 4, 5, 3, 3) or not np.isfinite(material_tangent).all():
                raise ValueError("material_tangent 必须为有限数值的 (nel, 4, 5, 3, 3) 数组")
            weighted_tangent = material_tangent * self.volume_weights[:, :, :, None, None]
            cb = weighted_tangent @ kinematics.strain_B
            local_tangent = np.einsum("eptia,eptib->eab", kinematics.strain_B, cb, optimize=True)
            local_tangent += np.einsum("epia,epib,ep->eab", self.shear_B, self.shear_B, shear_weights, optimize=True)
            # von Karman 应变的二阶导数只作用于 w，膜力包含全部五个厚度积分点。
            membrane_force = weighted_stress.sum(axis=2)
            force_tensor = np.empty((count, 4, 2, 2))
            force_tensor[:, :, 0, 0] = membrane_force[:, :, 0]
            force_tensor[:, :, 1, 1] = membrane_force[:, :, 1]
            force_tensor[:, :, 0, 1] = force_tensor[:, :, 1, 0] = membrane_force[:, :, 2]
            geometric = np.einsum("epia,epij,epjb->eab", self.gradients, force_tensor, self.gradients, optimize=True)
            local_tangent[:, 2::5, 2::5] += geometric
            matrix = coo_matrix((local_tangent.ravel(), (self._rows, self._columns)), shape=(self.ndof, self.ndof)).tocsr()
        return ShellResponse(None, residual, matrix)

    def _eigenstrain(self, eigenstrain):
        if eigenstrain is not None:
            eigenstrain = np.asarray(eigenstrain, dtype=float)
            count = len(self.connectivity)
            if eigenstrain.shape == (count, 3):
                eigenstrain = eigenstrain[:, None, None]
            elif eigenstrain.shape == (count, 5, 3):
                eigenstrain = eigenstrain[:, None]
            elif eigenstrain.shape == (count, 20, 3):
                eigenstrain = eigenstrain.reshape(count, 4, 5, 3)
            elif eigenstrain.shape != (count, 4, 5, 3):
                raise ValueError("eigenstrain 要求 (nel,3)、(nel,5,3)、(nel,20,3) 或 (nel,4,5,3)")
            if not np.isfinite(eigenstrain).all():
                raise ValueError("eigenstrain 包含非有限数值")
        return eigenstrain

    def _elastic_response(self, q, eigenstrain, section, *, tangent, energy_only):
        count = len(self.connectivity)
        membrane, curvature, shear, membrane_B = self._section_kinematics(q, not energy_only)
        if section is None:
            eigenstrain = self._eigenstrain(eigenstrain)
            mechanical = membrane[:, :, None, :] + self.thickness_coordinates[:, None, :, None] * curvature[:, :, None, :]
            if eigenstrain is not None:
                mechanical -= eigenstrain
            stress = np.einsum("eij,eptj->epti", self.elasticity, mechanical)
            energy = 0.5 * np.einsum("epti,epti,ept->", mechanical, stress, self.volume_weights)
            shear_weights = self._shear_weights(None)
            if not energy_only:
                weighted_stress = stress * self.volume_weights[:, :, :, None]
                membrane_force = weighted_stress.sum(axis=2)
                bending_moment = np.einsum("epti,et->epi", weighted_stress, self.thickness_coordinates)
        else:
            if eigenstrain is not None:
                raise ValueError("section 与 eigenstrain 不能同时提供")
            if not isinstance(section, ElasticSection) or len(section.stiffness) != count:
                raise ValueError("section 必须为元素数量一致的 ElasticSection")
            a, b, d = section.stiffness[:, 0], section.stiffness[:, 1], section.stiffness[:, 2]
            eigen_force, eigen_moment = section.eigen_resultants[:, 0, None], section.eigen_resultants[:, 1, None]
            membrane_force = (np.einsum("eij,epj->epi", a, membrane)
                              + np.einsum("eij,epj->epi", b, curvature) - eigen_force)
            bending_moment = (np.einsum("eij,epj->epi", b, membrane)
                              + np.einsum("eij,epj->epi", d, curvature) - eigen_moment)
            energy = 0.5 * np.einsum("epi,epi,ep->", membrane, membrane_force - eigen_force, self.area_weights)
            energy += 0.5 * np.einsum("epi,epi,ep->", curvature, bending_moment - eigen_moment, self.area_weights)
            energy += np.einsum("e,ep->", section.eigen_energy, self.area_weights)
            shear_weights = (5 / 6) * self.area_weights * section.shear_integral[:, None]
            if not energy_only:
                membrane_force *= self.area_weights[:, :, None]
                bending_moment *= self.area_weights[:, :, None]
        energy += 0.5 * np.einsum("epi,epi,ep->", shear, shear, shear_weights)
        if energy_only:
            return float(energy)
        local_residual = np.empty((count, 20))
        local_residual[:, _MEMBRANE_DOFS] = np.einsum("epia,epi->ea", membrane_B, membrane_force)
        local_residual[:, _BENDING_DOFS] = np.einsum("epia,epi->ea", self._bending_B, bending_moment)
        local_residual[:, _SHEAR_DOFS] += np.einsum("epia,epi,ep->ea", self._shear_B, shear, shear_weights)
        residual = np.bincount(self.element_dofs.ravel(), weights=local_residual.ravel(), minlength=self.ndof)
        matrix = None
        if tangent:
            # 五个厚度积分点形成 A/B/D；矩阵乘法只保留四个面内积分点。
            if section is None:
                coefficients = self._section_moments[:, :, :, None, None] * self.elasticity[:, None, None]
            else:
                coefficients = self.area_weights[:, :, None, None, None] * section.stiffness[:, None]
            a, b, d = coefficients[:, :, 0], coefficients[:, :, 1], coefficients[:, :, 2]
            membrane_transpose = membrane_B.reshape(count, 12, 12).swapaxes(1, 2)
            bending_transpose = self._bending_B.reshape(count, 12, 8).swapaxes(1, 2)
            local_tangent = np.empty((count, 20, 20))
            local_tangent[:, _MEMBRANE_DOFS[:, None], _MEMBRANE_DOFS] = membrane_transpose @ (a @ membrane_B).reshape(count, 12, 12)
            coupling = membrane_transpose @ (b @ self._bending_B).reshape(count, 12, 8)
            local_tangent[:, _MEMBRANE_DOFS[:, None], _BENDING_DOFS] = coupling
            local_tangent[:, _BENDING_DOFS[:, None], _MEMBRANE_DOFS] = coupling.swapaxes(1, 2)
            local_tangent[:, _BENDING_DOFS[:, None], _BENDING_DOFS] = bending_transpose @ (d @ self._bending_B).reshape(count, 12, 8)
            shear_transpose = self._shear_B.reshape(count, 8, 12).swapaxes(1, 2)
            local_tangent[:, _SHEAR_DOFS[:, None], _SHEAR_DOFS] += shear_transpose @ (self._shear_B * shear_weights[:, :, None, None]).reshape(count, 8, 12)
            force_tensor = np.empty((count, 4, 2, 2))
            force_tensor[:, :, 0, 0] = membrane_force[:, :, 0]
            force_tensor[:, :, 1, 1] = membrane_force[:, :, 1]
            force_tensor[:, :, 0, 1] = force_tensor[:, :, 1, 0] = membrane_force[:, :, 2]
            geometric = self.gradients.reshape(count, 8, 4).swapaxes(1, 2) @ (force_tensor @ self.gradients).reshape(count, 8, 4)
            local_tangent[:, 2::5, 2::5] += geometric
            matrix = coo_matrix((local_tangent.ravel(), (self._rows, self._columns)), shape=(self.ndof, self.ndof)).tocsr()
        return ShellResponse(float(energy), residual, matrix)

    def evaluate(self, q, eigenstrain=None, *, tangent=True, section=None):
        """弹性应变能、内力及一致切线；eigenstrain 可按元素、厚度或全部积分点提供。"""
        return self._elastic_response(q, eigenstrain, section, tangent=tangent, energy_only=False)

    def energy(self, q, eigenstrain=None, *, section=None):
        """仅计算弹性应变能，供线搜索使用。"""
        return self._elastic_response(q, eigenstrain, section, tangent=False, energy_only=True)
