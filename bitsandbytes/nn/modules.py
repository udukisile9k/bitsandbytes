# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
from typing import Optional, TypeVar, Union, overload

import torch
import torch.nn.functional as F
from torch import Tensor, device, dtype, nn

import bitsandbytes as bnb
from bitsandbytes.optim import GlobalOptimManager
from bitsandbytes.utils import OutlierTracer, find_outlier_dims

T = TypeVar("T", bound="torch.nn.Module")


class StableEmbedding(torch.nn.Embedding):
    def __init__(
        self,
        num_embeddings: int,
        embedding_dim: int,
        padding_idx: Optional[int] = None,
        max_norm: Optional[float] = None,
        norm_type: float = 2.0,
        scale_grad_by_freq: bool = False,
        sparse: bool = False,
        _weight: Optional[Tensor] = None,
        device=None,
        dtype=None,
    ) -> None:
        super().__init__(
            num_embeddings,
            embedding_dim,
            padding_idx,
            max_norm,
            norm_type,
            scale_grad_by_freq,
            sparse,
            _weight,
            device,
            dtype,
        )
        self.norm = torch.nn.LayerNorm(embedding_dim, device=device)
        GlobalOptimManager.get_instance().register_module_override(
            self, "weight", {"optim_bits": 32}
        )

    def reset_parameters(self) -> None:
        torch.nn.init.xavier_uniform_(self.weight)
        self._fill_padding_idx_with_zero()

    """ !!! This is a redefinition of _fill_padding_idx_with_zero in torch.nn.Embedding
        to make the Layer compatible with Pytorch < 1.9.
        This means that if this changes in future PyTorch releases this need to change too
        which is cumbersome. However, with this we can ensure compatibility with previous
        PyTorch releases.
    """

    def _fill_padding_idx_with_zero(self) -> None:
        if self.padding_idx is not None:
            with torch.no_grad():
                self.weight[self.padding_idx].fill_(0)

    def forward(self, input: Tensor) -> Tensor:
        emb = F.embedding(
            input,
            self.weight,
            self.padding_idx,
            self.max_norm,
            self.norm_type,
            self.scale_grad_by_freq,
            self.sparse,
        )

        # always apply layer norm in full precision
        emb = emb.to(torch.get_default_dtype())

        return self.norm(emb).to(self.weight.dtype)


class Embedding(torch.nn.Embedding):
    def __init__(
        self,
        num_embeddings: int,
        embedding_dim: int,
        padding_idx: Optional[int] = None,
        max_norm: Optional[float] = None,
        norm_type: float = 2.0,
        scale_grad_by_freq: bool = False,
        sparse: bool = False,
        _weight: Optional[Tensor] = None,
    ) -> None:
        super().__init__(
            num_embeddings,
            embedding_dim,
            padding_idx,
            max_norm,
            norm_type,
            scale_grad_by_freq,
            sparse,
            _weight,
        )
        GlobalOptimManager.get_instance().register_module_override(
            self, "weight", {"optim_bits": 32}
        )

    def reset_parameters(self) -> None:
        torch.nn.init.xavier_uniform_(self.weight)
        self._fill_padding_idx_with_zero()

    """ !!! This is a redefinition of _fill_padding_idx_with_zero in torch.nn.Embedding
        to make the Layer compatible with Pytorch < 1.9.
        This means that if this changes in future PyTorch releases this need to change too
        which is cumbersome. However, with this we can ensure compatibility with previous
        PyTorch releases.
    """

    def _fill_padding_idx_with_zero(self) -> None:
        if self.padding_idx is not None:
            with torch.no_grad():
                self.weight[self.padding_idx].fill_(0)

    def forward(self, input: Tensor) -> Tensor:
        emb = F.embedding(
            input,
            self.weight,
            self.padding_idx,
            self.max_norm,
            self.norm_type,
            self.scale_grad_by_freq,
            self.sparse,
        )

        return emb

class OutlierAwareLinear(nn.Linear):
    def __init__(self, input_features, output_features, bias=True):
        super().__init__(input_features, output_features, bias)
        self.outlier_dim = None
        self.is_quantized = False

    def forward_with_outliers(self, x, outlier_idx):
        raise NotImplementedError('Please override the `forward_with_outliers(self, x, outlier_idx)` function')

    def quantize_weight(self, w, outlier_idx):
        raise NotImplementedError('Please override the `quantize_weights(self, w, outlier_idx)` function')

    def forward(self, x):
        if self.outlier_dim is None:
            tracer = OutlierTracer.get_instance()
            if not tracer.is_initialized():
                print('Please use OutlierTracer.initialize(model) before using the OutlierAwareLinear layer')
            outlier_idx = tracer.get_outliers(self.weight)
            #print(outlier_idx, tracer.get_hvalue(self.weight))
            self.outlier_dim = outlier_idx

        if not self.is_quantized:
            w = self.quantize_weight(self.weight, self.outlier_dim)
            self.weight.data.copy_(w)
            self.is_quantized = True

        return self.forward_with_outliers(x, self.outlier_dim)


class Fake4bitLinear(OutlierAwareLinear):
    def __init__(self, input_features, output_features, bias=True, codebook=bnb.functional.create_fp8_map(True, 3, 0, total_bits=4)):
        super().__init__(input_features, output_features, bias)
        self.codebook = codebook

    def quantize_weight(self, w, outlier_idx):
        if outlier_idx.numel() > 0:
            subw = w[:, outlier_idx].clone()
            w[:, outlier_idx] = 0
        wdtype = w.dtype
        code = self.codebook.to(w.device)
        cw, state = bnb.functional.quantize_blockwise(w, code=code, blocksize=64)
        w = bnb.functional.dequantize_blockwise(cw, state, blocksize=64)
        w = w.to(wdtype)
        if outlier_idx.numel() > 0:
            w[:, outlier_idx] = subw
        self.is_quantized = True
        return w

    def forward_with_outliers(self, x, outlier_idx):
        dims = torch.abs(x> 4).sum(dim=list(range(len(x.shape)-1)))
        outlier_idx2 = torch.where(dims > 0)[0]
        outlier_idx = torch.cat([outlier_idx, outlier_idx2]).unique()
        n = x.shape[-1]
        idx = torch.arange(n, device=x.device)
        idx[outlier_idx] = -1
        inverse_idx = torch.where(idx >= 0)[0]
        if outlier_idx.numel() > 0:
            subx = x[..., outlier_idx].clone()
            #print(1, subx, 1)
            #x[..., outlier_idx] = 0
        inverse_x = x[...,inverse_idx]
        xdtype = x.dtype
        #code = bnb.functional.create_fp8_map(True, 4-3, 2, 4).to(x.device)
        #code = bnb.functional.create_quantile_map(x, 4).to(x.device)
        code = bnb.functional.create_dynamic_map(True, total_bits=4.0).to(x.device)
        c, state = bnb.functional.quantize_blockwise(inverse_x, code=code, blocksize=64)
        inverse_x = bnb.functional.dequantize_blockwise(c, state, blocksize=64)
        #c, state = bnb.functional.quantize_blockwise(x, code=code, blocksize=64)
        #x = bnb.functional.dequantize_blockwise(c, state, blocksize=64)
        x = x.to(xdtype)
        x[..., inverse_idx] = inverse_x.to(x.dtype)
        #if outlier_idx.numel() > 0:
            #x[..., outlier_idx] = subx

        return torch.nn.functional.linear(x, self.weight, self.bias)



class Int8Params(torch.nn.Parameter):
    def __new__(
        cls,
        data=None,
        requires_grad=True,
        has_fp16_weights=False,
        CB=None,
        SCB=None,
    ):
        cls.has_fp16_weights = has_fp16_weights
        cls.CB = None
        cls.SCB = None
        if data is None:
            data = torch.empty(0)
        return torch.Tensor._make_subclass(cls, data, requires_grad)

    def cuda(self, device):
        if self.has_fp16_weights:
            return super().cuda(device)
        else:
            # we store the 8-bit rows-major weight
            # we convert this weight to the turning/ampere weight during the first inference pass
            B = self.data.contiguous().half().cuda(device)
            CB, CBt, SCB, SCBt, coo_tensorB = bnb.functional.double_quant(B)
            del CBt
            del SCBt
            self.data = CB
            setattr(self, "CB", CB)
            setattr(self, "SCB", SCB)

        return self

    @overload
    def to(
        self: T,
        device: Optional[Union[int, device]] = ...,
        dtype: Optional[Union[dtype, str]] = ...,
        non_blocking: bool = ...,
    ) -> T:
        ...

    @overload
    def to(self: T, dtype: Union[dtype, str], non_blocking: bool = ...) -> T:
        ...

    @overload
    def to(self: T, tensor: Tensor, non_blocking: bool = ...) -> T:
        ...

    def to(self, *args, **kwargs):
        device, dtype, non_blocking, convert_to_format = torch._C._nn._parse_to(
            *args, **kwargs
        )

        if (
            device is not None
            and device.type == "cuda"
            and self.data.device.type == "cpu"
        ):
            return self.cuda(device)
        else:
            new_param = Int8Params(
                super().to(
                    device=device, dtype=dtype, non_blocking=non_blocking
                ),
                requires_grad=self.requires_grad,
                has_fp16_weights=self.has_fp16_weights,
            )
            new_param.CB = self.CB
            new_param.SCB = self.SCB

            return new_param


class Linear8bitLt(nn.Linear):
    def __init__(
        self,
        input_features,
        output_features,
        bias=True,
        has_fp16_weights=True,
        memory_efficient_backward=False,
        threshold=0.0,
        index=None,
    ):
        super().__init__(
            input_features, output_features, bias
        )
        self.state = bnb.MatmulLtState()
        self.index = index

        self.state.threshold = threshold
        self.state.has_fp16_weights = has_fp16_weights
        self.state.memory_efficient_backward = memory_efficient_backward
        if threshold > 0.0 and not has_fp16_weights:
            self.state.use_pool = True

        self.weight = Int8Params(
            self.weight.data, has_fp16_weights=has_fp16_weights, requires_grad=has_fp16_weights
        )

    def init_8bit_state(self):
        self.state.CB = self.weight.CB
        self.state.SCB = self.weight.SCB
        self.weight.CB = None
        self.weight.SCB = None

    def forward(self, x):
        self.state.is_training = self.training

        if self.weight.CB is not None:
            self.init_8bit_state()

        # weights are cast automatically as Int8Params, but the bias has to be cast manually
        # if self.bias is not None and self.bias.dtype != torch.float16:
        #     self.bias.data = self.bias.data.half()

        #out = bnb.matmul(x.half(), self.weight.half(), bias=None, state=self.state) + self.bias
        out = bnb.matmul(x.half(), self.weight.half(), bias=None, state=self.state) + self.bias

        if not self.state.has_fp16_weights:
            if not self.state.memory_efficient_backward and self.state.CB is not None:
                # we converted 8-bit row major to turing/ampere format in the first inference pass
                # we no longer need the row-major weight
                del self.state.CB
                self.weight.data = self.state.CxB
            elif self.state.memory_efficient_backward and self.state.CxB is not None:
                # For memory efficient backward, we convert 8-bit row major to turing/ampere format at each inference pass.
                # Thus, we delete CxB from the state.
                del self.state.CxB

        return out


class Linear8bitLtThresh(Linear8bitLt):
    def __init__(
        self,
        input_features,
        output_features,
        bias=True,
        has_fp16_weights=True,
        memory_efficient_backward=False,
        threshold=6.0,
        index=None,
    ):
        super().__init__(
            input_features, 
            output_features, 
            bias=bias, 
            has_fp16_weights=has_fp16_weights, 
            memory_efficient_backward=memory_efficient_backward, 
            threshold=threshold, 
            index=index
        )

class LinearFP8(nn.Linear):
    def __init__(self, input_features, output_features, bias=True):
        super().__init__(input_features, output_features, bias)
        self.bw_code = None
        self.fw_code = None

    def forward(self, x: torch.Tensor):
        if self.fw_code is None:
            self.bw_code = bnb.functional.create_fp8_map(True, 5, 2, 8).to(x.device)
            self.fw_code = bnb.functional.create_fp8_map(True, 4, 3, 8).to(x.device)

        out = bnb.matmul_fp8(x, self.weight.t(), fw_code=self.fw_code, bw_code=self.bw_code)
        if self.bias is not None:
            out += self.bias

        return out

class LinearInt8(nn.Linear):
    def __init__(self, input_features, output_features, bias=True):
        super().__init__(input_features, output_features, bias)
        self.code = None

    def forward(self, x: torch.Tensor):
        if self.code is None:
            self.code = bnb.functional.create_linear_map(True, 8).to(x.device)

        out = bnb.matmul_fp8(x, self.weight.t(), fw_code=self.code, bw_code=self.code)
        if self.bias is not None:
            out += self.bias

        return out

class LinearInt8Cast(nn.Linear):
    def __init__(self, input_features, output_features, bias=True):
        super().__init__(input_features, output_features, bias)
        self.code = None

    def forward(self, x: torch.Tensor):
        if self.code is None:
            self.code = bnb.functional.create_linear_map(True, 8).to(x.device)

        out = bnb.matmul_fp8(x.half(), self.weight.half().t(), fw_code=self.code, bw_code=self.code)
        if self.bias is not None:
            out += self.bias

        return out

