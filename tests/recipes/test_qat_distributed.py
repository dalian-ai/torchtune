# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import runpy

import sys
from pathlib import Path

import pytest
import torch
from tests.common import TUNE_PATH

from tests.recipes.utils import (
    CKPT_COMPONENT_MAP,
    dummy_alpaca_dataset_config,
    MODEL_TEST_CONFIGS,
    write_hf_ckpt_config,
)
from tests.test_utils import (
    CKPT_MODEL_PATHS,
    gen_log_file_name,
    get_loss_values_from_metric_logger,
    gpu_test,
    TOKENIZER_PATHS,
)


class TestQATDistributedRecipe:
    def _get_test_config_overrides(self):
        return [
            "dtype=fp32",
            "enable_activation_checkpointing=False",
            "enable_activation_offloading=False",
            "dataset.train_on_input=False",
            "seed=9",
            "epochs=2",
            "max_steps_per_epoch=2",
            "optimizer=torch.optim.AdamW",
            "optimizer.lr=2e-5",
            "log_every_n_steps=1",
        ] + dummy_alpaca_dataset_config()

    def _fetch_expected_loss_values(self, model_type):
        loss_values_map = {
            "llama3": [
                11.977460861206055,
                11.978384017944336,
                11.946539878845215,
                11.909686088562012,
            ],
        }
        return loss_values_map[model_type]

    @pytest.mark.integration_test
    @pytest.mark.parametrize(
        "config, model_type, ckpt_type, micro_batch_size, gradient_accumulation_steps",
        [
            ("llama3/8B_qat_full", "llama3", "tune", 4, 1),
            ("llama3/8B_qat_full", "llama3", "tune", 1, 4),
        ],
    )
    @gpu_test(gpu_count=4)
    def test_loss(
        self,
        config,
        model_type,
        ckpt_type,
        micro_batch_size,
        gradient_accumulation_steps,
        tmpdir,
        monkeypatch,
    ):
        ckpt_component = CKPT_COMPONENT_MAP[ckpt_type]
        ckpt = model_type + "_" + ckpt_type
        ckpt_path = Path(CKPT_MODEL_PATHS[ckpt])
        tokenizer_path = Path(TOKENIZER_PATHS[model_type])
        ckpt_dir = ckpt_path.parent
        log_file = gen_log_file_name(tmpdir)

        # Config file needed for model conversion.
        write_hf_ckpt_config(ckpt_dir)

        cmd = f"""
        tune run --nnodes 1 --nproc_per_node 4 qat_distributed \
            --config {config} \
            output_dir={tmpdir} \
            batch_size={micro_batch_size} \
            gradient_accumulation_steps={gradient_accumulation_steps} \
            checkpointer._component_={ckpt_component} \
            checkpointer.checkpoint_dir='{ckpt_dir}' \
            checkpointer.checkpoint_files=[{ckpt_path}]\
            checkpointer.output_dir={tmpdir} \
            checkpointer.model_type={model_type.upper()} \
            tokenizer.path='{tokenizer_path}' \
            tokenizer.prompt_template=null \
            metric_logger.filename={log_file} \
        """.split()
        model_config = MODEL_TEST_CONFIGS[model_type]
        cmd = cmd + self._get_test_config_overrides() + model_config

        monkeypatch.setattr(sys, "argv", cmd)
        runpy.run_path(TUNE_PATH, run_name="__main__")
        loss_values = get_loss_values_from_metric_logger(log_file)
        expected_loss_values = self._fetch_expected_loss_values(model_type)

        torch.testing.assert_close(
            loss_values, expected_loss_values, rtol=1e-3, atol=1e-3
        )
