from opt import get_opts
import wandb

# pytorch-lightning
from pytorch_lightning import Trainer
from pytorch_lightning.callbacks import ModelCheckpoint, TQDMProgressBar
from pytorch_lightning.plugins import DDPPlugin
from pytorch_lightning.loggers import WandbLogger
import pytorch_lightning as pl

wandb_logger = WandbLogger()
import numpy as np

from models.vanilla_nerf.model import LitNeRF
from models.vanilla_nerf.model_ae_art import LitNeRF_AE_ART
from models.vanilla_nerf.model_autodecoder import LitNeRF_AutoDecoder


def main(hparams):
    if hparams.exp_type == "vanilla":
        system = LitNeRF(
            hparams=hparams
        )  # Needs to modify this to train for 3 test images

    elif hparams.exp_type == "vanilla_ae_art":
        system = LitNeRF_AE_ART(
            hparams=hparams
        )  # Needs to modify this to train for 3 test images

    elif hparams.exp_type == "vanilla_autodecoder":
        system = LitNeRF_AutoDecoder(
            hparams=hparams
        )  # Needs to modify this to train for 3 test images

    if hparams.is_optimize is not None:
        num = int(hparams.is_optimize[0])
        ckpt_cb = ModelCheckpoint(
            dirpath=f"/content/articulated-object-nerf/ckpts/{hparams.exp_name}",
            monitor="val/psnr",
            filename=f"optimize_{num}_{{epoch:d}}",
            save_top_k=-1,
            mode="max",
            save_last=False,
            every_n_epochs=1,
            # every_n_epochs=50,
        )

    elif hparams.finetune_lpips:
        ckpt_cb = ModelCheckpoint(
            dirpath=f"/content/articulated-object-nerf/ckpts/{hparams.exp_name}",
            monitor="val/psnr",
            filename="finetune_lpips_{epoch:d}",
            save_top_k=5,
            mode="max",
            save_last=False,
            every_n_epochs=1,
            # every_n_epochs=50,
        )
    else:
        ckpt_cb = ModelCheckpoint(
            dirpath=f"/content/articulated-object-nerf/ckpts/{hparams.exp_name}",
            monitor="val/psnr",
            filename="{epoch:d}",
            save_top_k=5,
            mode="max",
            save_last=True,
            every_n_epochs=10,
            # every_n_epochs=50,
        )

    pbar = TQDMProgressBar(refresh_rate=1)
    callbacks = [ckpt_cb, pbar]
    wandb_logger = WandbLogger()

    if hparams.finetune_lpips or hparams.is_optimize:
        if hparams.ckpt_path is not None:
            ckpt_path = (
                f"/content/articulated-object-nerf/ckpts/{hparams.exp_name}/{hparams.ckpt_path}"
            )
        else:
            ckpt_path = f"/content/articulated-object-nerf/ckpts/{hparams.exp_name}/last.ckpt"
    else:
        ckpt_path = None
    if hparams.is_optimize:
        if hparams.finetune_lpips:
            find_unused_parameters = True
        else:
            find_unused_parameters = False

        trainer = Trainer(
            max_epochs=hparams.num_epochs,
            callbacks=callbacks,
            resume_from_checkpoint=ckpt_path,
            logger=wandb_logger,
            enable_model_summary=True,
            # accelerator='auto',
            # precision=16,
            log_every_n_steps=5,
            accelerator="gpu",
            devices=hparams.num_gpus,
            num_sanity_val_steps=1,
            detect_anomaly=True,
            benchmark=False,
            check_val_every_n_epoch=1,
            limit_val_batches=5,  # for single scene scenario
            profiler="simple" if hparams.num_gpus == 1 else None,
            strategy=DDPPlugin(find_unused_parameters=find_unused_parameters)
            if hparams.num_gpus > 1
            else None,
        )

    elif hparams.finetune_lpips:
        trainer = Trainer(
            max_epochs=hparams.num_epochs,
            callbacks=callbacks,
            resume_from_checkpoint=ckpt_path,
            logger=wandb_logger,
            enable_model_summary=True,
            accelerator="gpu",
            devices=hparams.num_gpus,
            num_sanity_val_steps=1,
            detect_anomaly=True,
            benchmark=False,
            check_val_every_n_epoch=1,
            limit_val_batches=5,  # for single scene scenario
            profiler="simple" if hparams.num_gpus == 1 else None,
            strategy=DDPPlugin(find_unused_parameters=True)
            if hparams.num_gpus > 1
            else None,
        )
    else:
        # SET UNUSED PARAMETERS TO FALSEEE
        trainer = Trainer(
            max_epochs=hparams.num_epochs,
            callbacks=callbacks,
            resume_from_checkpoint=ckpt_path,
            logger=wandb_logger,
            enable_model_summary=True,
            accelerator="gpu",
            # gradient_clip_val = 0.5,
            devices=hparams.num_gpus,
            num_sanity_val_steps=1,
            detect_anomaly=True,
            benchmark=False,
            check_val_every_n_epoch=1,
            limit_val_batches=5,  # for single scene scenario,
            # profiler="simple" if hparams.num_gpus==1 else None,
            # profiler=profiler,
            strategy=DDPPlugin(find_unused_parameters=False)
            if hparams.num_gpus > 1
            else None,
        )

    if hparams.run_eval:
        if hparams.ckpt_path is not None:
            ckpt_path = (
                f"/content/articulated-object-nerf/ckpts/{hparams.exp_name}/{hparams.ckpt_path}"
            )
        else:
            ckpt_path = f"/content/articulated-object-nerf/ckpts/{hparams.exp_name}/last.ckpt"
        trainer.test(system, ckpt_path=ckpt_path)
        # self.val_dataset = dataset(split='val', **kwargs_test)
    else:
        trainer.fit(system)


if __name__ == "__main__":
    hparams = get_opts()
    main(hparams)
