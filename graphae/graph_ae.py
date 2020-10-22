import torch
from graphae import encoder, decoder, permuter, sinkhorn_ops
from graphae.fully_connected import FNN
from time import time


class Encoder(torch.nn.Module):
    def __init__(self, hparams):
        super().__init__()
        self.stack_node_emb = hparams["stack_node_emb"]

        self.graph_encoder = encoder.GraphEncoder(
            input_dim=hparams["num_node_features"],
            hidden_dim=hparams["graph_encoder_hidden_dim_gnn"],
            node_dim=hparams["node_dim"],
            num_nodes=hparams["max_num_nodes"],
            num_edge_features=hparams["num_edge_features"],
            num_layers=hparams["graph_encoder_num_layers_gnn"],
            batch_norm=hparams["batch_norm"],
            non_linearity=hparams["nonlin"],
            stack_node_emb=hparams["stack_node_emb"]
        )
        self.node_aggregator = encoder.NodeAggregator(
            input_dim=hparams["graph_encoder_num_layers_gnn"] * hparams["node_dim"] if hparams["stack_node_emb"] else hparams["node_dim"],
            emb_dim=hparams["graph_emb_dim"],
            hidden_dim=hparams["graph_encoder_hidden_dim_fnn"],
            num_layers=hparams["graph_encoder_num_layers_fnn"],
            batch_norm=hparams["batch_norm"],
            non_linearity=hparams["nonlin"]
        )

    def forward(self, graph):
        node_embs = self.graph_encoder(graph)
        assert graph.batch is not None  # TODO: implement torch.ones batch for single graph case
        graph_emb = self.node_aggregator(node_embs, batch_idxs=graph.batch)
        if self.stack_node_emb:
            node_embs = node_embs[:, :, -1]  # just take output (node emb) of the last layer
        return graph_emb, node_embs


class Decoder(torch.nn.Module):
    def __init__(self, hparams):
        super().__init__()
        self.node_emb_predictor = decoder.NodeEmbDecoder(
            emb_dim=hparams["graph_emb_dim"],
            node_dim=hparams["node_dim"],
            hidden_dim=hparams["meta_node_decoder_hidden_dim"],
            num_nodes=hparams["max_num_nodes"],
            num_layers_fnn=hparams["meta_node_decoder_num_layers_fnn"],
            num_layers_rnn=hparams["meta_node_decoder_num_layers_rnn"],
            non_lin=hparams["nonlin"],
            batch_norm=hparams["batch_norm"],
        )
        self.edge_predictor = decoder.EdgeDecoder(
            node_dim=hparams["node_dim"],
            hidden_dim=hparams["edge_predictor_hidden_dim"],
            num_nodes=hparams["max_num_nodes"],
            num_layers=hparams["edge_predictor_num_layers"],
            num_edge_features=hparams["num_edge_features"],
            non_lin=hparams["nonlin"],
            batch_norm=hparams["batch_norm"],
        )
        self.node_predictor = decoder.NodePredictor(
            node_dim=hparams["node_dim"],
            hidden_dim=hparams["node_decoder_hidden_dim"],
            num_layers=hparams["node_decoder_num_layers"],
            batch_norm=hparams["batch_norm"],
            num_node_features=hparams["num_node_features"],
            non_lin=hparams["nonlin"]
        )

    def forward(self, graph_emb, node_emb_encoded, teacher_forcing):
        node_embs = self.node_emb_predictor(graph_emb, node_emb_encoded, teacher_forcing)
        node_logits = self.node_predictor(node_embs)
        adj_logits = self.edge_predictor(node_embs)
        return node_logits, adj_logits, node_embs


class GraphAE(torch.nn.Module):
    def __init__(self, hparams):
        super().__init__()
        self.num_nodes = hparams["max_num_nodes"]
        self.encoder = Encoder(hparams)
        self.decoder = Decoder(hparams)

    def forward(self, graph, teacher_forcing=0.0, postprocess_method=None, postprocess_temp=1.0):
        graph_emb, node_emb_enc = self.encoder(graph=graph)
        node_emb_enc = node_embs_to_dense(
            node_embs=node_emb_enc,
            num_nodes=self.num_nodes,
            batch_idxs=graph.batch
        )
        node_logits, adj_logits, node_emb_dec = self.decoder(
            graph_emb=graph_emb,
            node_emb_encoded=node_emb_enc,
            teacher_forcing=teacher_forcing
        )
        mask_logits, node_logits = node_logits[:, :, 0], node_logits[:, :, 1:]
        if postprocess_method is not None:
            node_logits, adj_logits = self.postprocess_logits(
                node_logits=node_logits,
                adj_logits=adj_logits,
                method=postprocess_method,
                temp=postprocess_temp
            )
        return node_logits, adj_logits, mask_logits, node_emb_enc, node_emb_dec

    @staticmethod
    def postprocess_logits(node_logits, adj_logits, method=None, temp=1.0):
        element_type = node_logits[:, :, :11]
        charge_type = node_logits[:, :, 11:16]
        hybridization_type = node_logits[:, :, 16:]
        element_type = postprocess(element_type, method=method, temperature=temp)
        charge_type = postprocess(charge_type, method=method, temperature=temp)
        hybridization_type = postprocess(hybridization_type, method=method, temperature=temp)
        nodes = torch.cat((element_type, charge_type, hybridization_type), dim=-1)
        adj = postprocess(adj_logits, method=method)
        return nodes, adj

    @staticmethod
    def logits_to_one_hot(nodes, adj):
        batch_size, num_nodes = nodes.size(0), nodes.size(1)
        element_type = torch.argmax(nodes[:, :, :11], axis=-1).unsqueeze(-1)
        element_type = torch.zeros((batch_size, num_nodes, 11)).type_as(element_type).scatter_(2, element_type, 1)
        charge_type = torch.argmax(nodes[:, :, 11:16], axis=-1).unsqueeze(-1)
        charge_type = torch.zeros((batch_size, num_nodes, 5)).type_as(charge_type).scatter_(2, charge_type, 1)
        hybridization_type = torch.argmax(nodes[:, :, 16:], axis=-1).unsqueeze(-1)
        hybridization_type = torch.zeros((batch_size, num_nodes, 7)).type_as(hybridization_type).scatter_(2, hybridization_type, 1)
        nodes = torch.cat((element_type, charge_type, hybridization_type), dim=-1)
        adj_shape = adj.shape
        adj = torch.argmax(adj, axis=-1).unsqueeze(-1)
        adj = torch.zeros(adj_shape).type_as(adj).scatter_(3, adj, 1)
        return nodes, adj


def reparameterize(mu, logvar):
    std = torch.exp(0.5 * logvar)
    eps = torch.randn_like(std)
    return mu + eps * std


def node_embs_to_dense(node_embs, num_nodes, batch_idxs):
    num_features = node_embs.shape[-1]
    batch_size = batch_idxs.max().item() + 1
    device = node_embs.device
    mask = torch.where(
        torch.arange(num_nodes, device=device).unsqueeze(0) < torch.bincount(batch_idxs).unsqueeze(1),
        torch.ones(batch_size, num_nodes, device=device),
        torch.zeros(batch_size, num_nodes, device=device)
    ).bool().view(batch_size, num_nodes, 1).repeat(1, 1, num_features)
    node_embs_dense = torch.zeros(
        [batch_size, num_nodes, num_features],
        device=device).masked_scatter_(mask, node_embs)
    return node_embs_dense


def postprocess(logits, method, temperature=1.):
    if method == 'soft_gumbel':
        out = torch.nn.functional.gumbel_softmax(
            logits=logits,
            hard=False,
            tau=temperature,
            dim=-1
        )
    elif method == 'hard_gumbel':
        out = torch.nn.functional.gumbel_softmax(
            logits=logits,
            hard=True,
            tau=temperature,
            dim=-1
        )
    elif method == "softmax":
        out = torch.nn.functional.softmax(
            input=logits,
            dim=-1
        )
    else:
        raise NotImplementedError
    return out



