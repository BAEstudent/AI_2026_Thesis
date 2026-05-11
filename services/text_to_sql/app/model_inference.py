import os
from typing import Dict, List
import torch
import torch.nn as nn
from transformers import AutoModel, AutoTokenizer, T5ForConditionalGeneration


class QuestionGuidedSchemaSelector(nn.Module):
    def __init__(self, bert, tokenizer, device, hidden_size, lambda_coef=0.8):
        super().__init__()
        self.encoder = bert
        self.tokenizer = tokenizer
        self.device = device
        self.lambda_coef = lambda_coef
        self.q_proj = nn.Linear(hidden_size, hidden_size, bias=False)
        self.s_proj = nn.Linear(hidden_size, hidden_size, bias=False)

    def _encode(self, text):
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=64).to(self.device)
        with torch.no_grad():
            out = self.encoder(**inputs)
        return out.last_hidden_state[:, 0, :].squeeze(0)

    def forward(self, question, schema):
        tables = list(schema.keys())
        q_emb = self._encode(question)
        q_vec = self.q_proj(q_emb)

        if tables:
            t_embs = torch.stack([self._encode(t) for t in tables])
            t_vecs = self.s_proj(t_embs)
            table_logits = (t_vecs @ q_vec) / (q_vec.size(-1) ** 0.5)
        else:
            table_logits = torch.empty(0, device=self.device)

        col_logits_list = []
        for t in tables:
            cols = schema[t]
            if not cols:
                col_logits_list.append(torch.empty(0, device=self.device))
                continue
            c_embs = torch.stack([self._encode(c) for c in cols])
            c_vecs = self.s_proj(c_embs)
            c_logits = (c_vecs @ q_vec) / (q_vec.size(-1) ** 0.5)
            col_logits_list.append(c_logits)

        return table_logits, col_logits_list


class ComplexityClassifier(nn.Module):
    def __init__(self, encoder, hidden_size, num_classes=3):
        super().__init__()
        self.encoder = encoder
        self.head = nn.Linear(hidden_size, num_classes)

    def forward(self, input_ids, attention_mask):
        out = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        return self.head(out.last_hidden_state[:, 0, :])


class TriSQLInference:
    def __init__(self, model_dir="/app/models"):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # Selector
        self.selector_bert = AutoModel.from_pretrained(
            os.path.join(model_dir, "selector", "bert")
        ).to(self.device)
        self.selector_tokenizer = AutoTokenizer.from_pretrained(
            os.path.join(model_dir, "selector", "bert")
        )
        self.selector = QuestionGuidedSchemaSelector(
            bert=self.selector_bert,
            tokenizer=self.selector_tokenizer,
            device=self.device,
            hidden_size=self.selector_bert.config.hidden_size,
            lambda_coef=0.8,
        ).to(self.device)
        self.selector.load_state_dict(
            torch.load(os.path.join(model_dir, "selector", "model.pt"), map_location=self.device)
        )
        self.selector.eval()

        # Generator
        self.generator_tokenizer = AutoTokenizer.from_pretrained(
            os.path.join(model_dir, "generator", "tokenizer")
        )
        self.generator = T5ForConditionalGeneration.from_pretrained(
            os.path.join(model_dir, "generator", "model")
        ).to(self.device)
        self.generator.eval()

        # Classifier (optional, not used in final SQL)
        self.classifier_bert = AutoModel.from_pretrained(
            os.path.join(model_dir, "classifier", "bert")
        ).to(self.device)
        self.classifier_tokenizer = AutoTokenizer.from_pretrained(
            os.path.join(model_dir, "classifier", "bert")
        )
        self.classifier = ComplexityClassifier(
            encoder=self.classifier_bert,
            hidden_size=self.classifier_bert.config.hidden_size,
        ).to(self.device)
        self.classifier.load_state_dict(
            torch.load(os.path.join(model_dir, "classifier", "model.pt"), map_location=self.device)
        )
        self.classifier.eval()

        self.tau = 0.6
        self.lambda_coef = 0.8

    def _select_schema(self, question, schema):
        with torch.no_grad():
            t_logits, c_logits_list = self.selector(question, schema)

        if len(t_logits) == 0:
            return schema

        tables = list(schema.keys())
        filtered = {}
        for i, t in enumerate(tables):
            t_score = torch.sigmoid(t_logits[i]).item()
            cols = schema[t]
            if cols:
                col_scores = torch.sigmoid(c_logits_list[i])
                aggregated = t_score + self.lambda_coef * col_scores.sum().item()
            else:
                aggregated = t_score
            if aggregated >= self.tau:
                filtered[t] = cols

        if not filtered:
            best_idx = torch.argmax(t_logits).item()
            best_table = tables[best_idx]
            filtered[best_table] = schema[best_table]
        return filtered

    def _format_schema(self, schema):
        return " | ".join(f"{t}({', '.join(cols)})" for t, cols in schema.items())

    def _generate_skeleton(self, question, schema_text):
        source = f"question: {question} schema: {schema_text}"
        inputs = self.generator_tokenizer(
            source, return_tensors="pt", truncation=True, max_length=512
        ).to(self.device)
        with torch.no_grad():
            outputs = self.generator.generate(
                **inputs, max_new_tokens=128, num_beams=1, do_sample=False,
            )
        return self.generator_tokenizer.decode(outputs[0], skip_special_tokens=True)

    def _generate_sql(self, question, skeleton, schema_text):
        source = f"question: {question} skeleton: {skeleton} schema: {schema_text}"
        inputs = self.generator_tokenizer(
            source, return_tensors="pt", truncation=True, max_length=512
        ).to(self.device)
        with torch.no_grad():
            outputs = self.generator.generate(
                **inputs, max_new_tokens=256, num_beams=1, do_sample=False,
            )
        return self.generator_tokenizer.decode(outputs[0], skip_special_tokens=True)

    def predict(self, question: str, schema: Dict[str, List[str]]) -> Dict:
        filtered_schema = self._select_schema(question, schema)
        schema_text = self._format_schema(filtered_schema)
        skeleton = self._generate_skeleton(question, schema_text)
        sql = self._generate_sql(question, skeleton, schema_text)
        return {"sql": sql, "selected_schema": filtered_schema, "skeleton": skeleton}
