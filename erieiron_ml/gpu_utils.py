import subprocess
import time
from contextlib import contextmanager
from functools import lru_cache

import numpy as np
import psutil
import torch

from erieiron_common import settings_common
from erieiron_common.enums import ErieEnum


class ComputeDevice(ErieEnum):
    CPU = "cpu"
    MPS = "mps"
    CUDA = "cuda"

    def __str__(self):
        return self.value

    @classmethod
    def has_gpu(cls, device: 'ComputeDevice'):
        return ComputeDevice.MPS.eq(device) or ComputeDevice.CUDA.eq(device)


@contextmanager
def gpu_resource_manager(device_ids=None, no_grad=True):
    yield

    # if device_ids is None:
    #     device_ids = [0]
    #
    # if torch.cuda.is_available():
    #     try:
    #         for device_id in device_ids:
    #             torch.cuda.set_device(device_id)
    #         if no_grad:
    #             with torch.no_grad():
    #                 yield
    #         else:
    #             yield
    #     except Exception as e:
    #         common.log_error(f"An error occurred while using CUDA: {e}")
    #         raise
    #     finally:
    #         gpu_cleanup()
    #
    # else:
    #     yield


def gpu_cleanup():
    if not torch.cuda.is_available():
        return

    # try:
    #     torch.cuda.empty_cache()
    #     common.log_info("Successfully cleaned up torch")
    # except Exception as e2:
    #     common.log_error(f"An error occurred while cleaning up CUDA: {e2}")

    # try:
    #     tf.keras.backend.clear_session()
    #     common.log_info("Successfully cleaned up tensorflow")
    # except:
    #     common.log_error("failed to cleanup tensorflow")


def get_free_gpu_memory():
    if True or not torch.cuda.is_available():
        return -1

    # this can hang when called by multiple threads
    try:
        # Run nvidia-smi command to get memory information
        result = subprocess.run(['nvidia-smi', '--query-gpu=memory.free,memory.total', '--format=csv,noheader,nounits'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Error running nvidia-smi: {result.stderr.strip()}")

        # Parse the result
        return [100 * float(x.split(',')[0]) / float(x.split(',')[1]) for x in result.stdout.strip().split('\n')][0]
    except Exception as e:
        print(f"Error: {e}")
        return -1


@lru_cache
def supported_device() -> ComputeDevice:
    if torch.backends.mps.is_available() and settings_common.ALLOW_MPS_DEVICE:
        return ComputeDevice.MPS
    elif torch.cuda.is_available():
        return ComputeDevice.CUDA
    else:
        return ComputeDevice.CPU


@lru_cache
def get_compute_device() -> ComputeDevice:
    if torch.backends.mps.is_available():
        return ComputeDevice.MPS
    elif cuda_health_checker.can_run_cuda_op():
        return ComputeDevice.CUDA
    else:
        return ComputeDevice.CPU


def get_device() -> str:
    return get_compute_device().value


def get_torch_device():
    return torch.device(get_device())


def get_mps_not_supported_tensor(y: torch.Tensor):
    if ComputeDevice(y.device.type) == ComputeDevice.MPS:
        return y.to(ComputeDevice.CPU.value)
    else:
        return y


class CudaHealthChecker:
    def __init__(self, cache_seconds=1):
        self.cache_seconds = cache_seconds
        self._last_check = 0
        self._last_result = False

    def can_run_cuda_op(self):
        now = time.time()
        if now - self._last_check < self.cache_seconds:
            return self._last_result

        self._last_check = now
        if not torch.cuda.is_available():
            self._last_result = False
            return False

        try:
            torch.empty(1, device="cuda").fill_(1.0)
            torch.cuda.synchronize()
            self._last_result = True
        except RuntimeError:
            self._last_result = False

        return self._last_result


def plot_tensor(y: torch.Tensor, title="untitled"):
    import matplotlib.pyplot as plt
    plt.plot(tensor_to_numpy(y))
    plt.title(title)
    plt.show()


def tensor_to_numpy(t: torch.Tensor) -> np.ndarray:
    if t.requires_grad:
        t = t.detach()

    t = t.contiguous()
    n = t.cpu().numpy()

    if t.dtype != torch.float64:
        n = n.astype(np.float64)

    return n.copy()


def ensure_dims(tensor: torch.Tensor, dims):
    while tensor.ndim > dims:
        tensor = tensor.squeeze(0)

    while tensor.ndim < dims:
        tensor = tensor.unsqueeze(0)

    return tensor


def get_gpu_used_percent() -> int:
    RETURN_VAL_NO_GPU = 100

    device = get_device()

    if ComputeDevice.CUDA.eq(device):
        free_mem, total_mem = torch.cuda.mem_get_info()
    elif ComputeDevice.MPS.eq(device):
        vm = psutil.virtual_memory()
        total_mem = vm.total
        free_mem = vm.available

    else:
        return RETURN_VAL_NO_GPU

    mem_used = total_mem - free_mem
    return (mem_used * 100) // total_mem if total_mem else RETURN_VAL_NO_GPU


# global singleton
cuda_health_checker = CudaHealthChecker()
