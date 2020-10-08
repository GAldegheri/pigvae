import pytorch_lightning as pl
import pandas as pd
from torch_geometric.data import DataLoader
from graphae.graph_ae import GraphAE
from graphae.data import MolecularGraphDatasetFromSmiles, batch_to_dense
from graphae.metrics import *
from time import time


class PLGraphAE(pl.LightningModule):

    def __init__(self, hparams):
        super().__init__()
        self.hparams = hparams
        self.graph_ae = GraphAE(hparams)
        self.critic = GraphReconstructionLoss(num_node_types=11, num_edge_types=5)

    def forward(self, graph, training=True):
        node_logits, adj_logits, mask_logits, perms = self.graph_ae(graph, training)
        return node_logits, adj_logits, mask_logits, perms

    def prepare_data(self):
        num_smiles = 1000000 if self.hparams["test"] else None
        smiles_df = pd.read_csv("smiles_{}_atoms.csv".format(self.hparams["max_num_nodes"]), nrows=num_smiles)
        self.train_dataset = MolecularGraphDatasetFromSmiles(
            smiles_list=smiles_df.iloc[self.hparams["num_eval_samples"]:].smiles.tolist(),
            num_nodes=self.hparams["max_num_nodes"],
        )
        self.eval_dataset = MolecularGraphDatasetFromSmiles(
            smiles_list=smiles_df.iloc[:self.hparams["num_eval_samples"]].smiles.tolist(),
            num_nodes=self.hparams["max_num_nodes"],
        )

    def train_dataloader(self):
        return DataLoader(
            dataset=self.train_dataset,
            batch_size=self.hparams["batch_size"],
            num_workers=self.hparams["num_workers"],
            shuffle=True,
            pin_memory=True
        )

    def val_dataloader(self):
        return DataLoader(
            dataset=self.eval_dataset,
            batch_size=self.hparams["batch_size"],
            num_workers=self.hparams["num_workers"],
            shuffle=False,
            pin_memory=True
        )

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.graph_ae.parameters())
        lr_scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer=optimizer,
            factor=0.5,
            patience=5,
            cooldown=10,
            min_lr=1e-6,
        )
        scheduler = {
            'scheduler': lr_scheduler,
            'interval': 'step',
            'frequency': self.hparams["eval_freq"] + 1
        }

        return [optimizer], [scheduler]

    def training_step(self, batch, batch_idx):
        sparse_graph, dense_graph = batch[0], batch[1]
        nodes_pred, adj_pred, mask_pred, _ = self(graph=sparse_graph, training=True)
        nodes_true, adj_true, mask_true = dense_graph.x, dense_graph.adj, dense_graph.mask
        loss = self.critic(
            nodes_true=nodes_true,
            adj_true=adj_true,
            mask_true=mask_true,
            nodes_pred=nodes_pred,
            adj_pred=adj_pred,
            mask_pred=mask_pred
        )
        return loss

    def validation_step(self, batch, batch_idx):
        sparse_graph, dense_graph = batch[0], batch[1]
        nodes_pred, adj_pred, mask_pred, _ = self(graph=sparse_graph, training=True)
        nodes_true, adj_true, mask_true = dense_graph.x, dense_graph.adj, dense_graph.mask
        loss = self.critic(
            nodes_true=nodes_true,
            adj_true=adj_true,
            mask_true=mask_true,
            nodes_pred=nodes_pred,
            adj_pred=adj_pred,
            mask_pred=mask_pred
        )
        #nodes_pred_oh, adj_pred_oh = self.graph_ae.logits_to_one_hot(nodes_pred, adj_pred)
        element_type_acc, charge_type_acc, hybridization_type_acc = node_balanced_accuracy(
            nodes_pred=nodes_pred,
            nodes_true=nodes_true,
            mask=mask_true
        )
        adj_acc = adj_balanced_accuracy(
            adj_pred=adj_pred,
            adj_true=adj_true,
            mask=mask_true
        )
        mask_acc = mask_balenced_accuracy(
            mask_pred=mask_pred,
            mask_true=mask_true
        )
        output = {
            **loss,
            "element_type_acc": element_type_acc,
            "charge_type_acc": charge_type_acc,
            "hybridization_type_acc": hybridization_type_acc,
            "adj_acc": adj_acc,
            "mask_acc": mask_acc
        }
        return output

    def validation_epoch_end(self, outputs):
        out = {}
        for key in outputs[0].keys():
            out[key] = torch.stack([output[key] for output in outputs]).mean()
        tqdm_dict = {'val_loss': out["loss"]}
        return {'val_loss': out["loss"], 'log': out, "progress_bar": tqdm_dict}




