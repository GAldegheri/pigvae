import torch
from graphae import encoder, decoder
from graphae.fully_connected import FNN


class Encoder(torch.nn.Module):
    def __init__(self, hparams):
        super().__init__()
        self.graph_encoder = encoder.GraphEncoder(
            input_dim=hparams["num_atom_features"],
            hidden_dim=hparams["graph_encoder_hidden_dim"],
            node_dim=hparams["node_dim"],
            emb_dim=hparams["emb_dim"],
            num_layers=hparams["graph_encoder_num_layers"],
            batch_norm=False,
            non_linearity="lrelu"
        )

    def forward(self, node_features, adj, mask):
        mol_emb = self.graph_encoder(node_features, adj, mask)
        return mol_emb


class Decoder(torch.nn.Module):
    def __init__(self, hparams):
        super().__init__()
        self.fnn = FNN(
            input_dim=hparams["emb_dim"],
            hidden_dim=hparams["meta_node_decoder_hidden_dim"],
            output_dim=hparams["meta_node_decoder_hidden_dim"],
            num_layers=hparams["meta_node_decoder_num_layers"],
            non_linearity="relu",
            batch_norm=hparams["batch_norm"],
        )
        self.edge_predictor = decoder.EdgePredictor(
            num_nodes=hparams["max_num_nodes"],
            input_dim=hparams["meta_node_decoder_hidden_dim"],
            hidden_dim=hparams["edge_predictor_hidden_dim"],
            num_layers=hparams["edge_predictor_num_layers"],
            batch_norm=hparams["batch_norm"],
        )
        self.node_predictor = decoder.NodePredictor(
            num_nodes=hparams["max_num_nodes"],
            input_dim=hparams["meta_node_decoder_hidden_dim"],
            hidden_dim=hparams["node_decoder_hidden_dim"],
            num_layers=hparams["node_decoder_num_layers"],
            batch_norm=hparams["batch_norm"],
            num_node_features=hparams["num_atom_features"]
        )

    def forward(self, emb):
        x = self.fnn(emb)
        edge_logits = self.edge_predictor(x)
        node_logits = self.node_predictor(x)
        return node_logits, edge_logits


class Descriminator(torch.nn.Module):
    def __init__(self, hparams):
        super().__init__()
        self.encoder = Encoder(hparams)
        self.fnn = FNN(
            #input_dim=hparams["graph_encoder_num_layers"] * hparams["node_dim"],
            input_dim=hparams["emb_dim"],
            hidden_dim=256,
            output_dim=1,
            num_layers=3,
            non_linearity="lrelu",
            batch_norm=False,
        )

    def forward(self, node, adj, mask):
        x = self.encoder(node, adj, mask)
        x = self.fnn(x)
        return x


class GraphAE(torch.nn.Module):
    def __init__(self, hparams):
        super().__init__()
        self.encoder = Encoder(hparams)
        self.decoder = Decoder(hparams)

    def forward(self, node_features, adj, mask):
        mol_emb = self.encoder(node_features, adj, mask)
        node_features_, adj_, mask_ = self.decoder(mol_emb)

        return node_features_, adj_, mask_


def reparameterize(mu, logvar):
    std = torch.exp(0.5 * logvar)
    eps = torch.randn_like(std)
    return mu + eps * std


