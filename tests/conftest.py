import pytest
import torch


def pytest_runtest_call(item):
    try:
        item.runtest()
    except NotImplementedError as nie:
        if "NO_CUBLASLT" in str(nie):
            pytest.skip("CUBLASLT not available")
        raise
    except AssertionError as ae:
        if str(ae) == "Torch not compiled with CUDA enabled":
            pytest.skip("Torch not compiled with CUDA enabled")
        raise


@pytest.fixture(scope="session")
def requires_cuda() -> bool:
    cuda_available = torch.cuda.is_available()
    if not cuda_available:
        pytest.skip("CUDA is required")
    return cuda_available
