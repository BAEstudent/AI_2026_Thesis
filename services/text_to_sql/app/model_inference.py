import os
from typing import Dict, List, Optional
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

        # Classifier
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

        # Optional: DB executor for real execution checks (set externally)
        self.db_executor = None

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

    # ------------------------- Refinement methods -------------------------
    def _classify_complexity(self, question: str, sql: str, schema_text: str) -> str:
        input_text = f"question: {question} sql: {sql} schema: {schema_text}"
        enc = self.classifier_tokenizer(
            input_text,
            truncation=True,
            padding="max_length",
            max_length=512,
            return_tensors="pt",
        )
        input_ids = enc["input_ids"].to(self.device)
        attention_mask = enc["attention_mask"].to(self.device)

        with torch.no_grad():
            logits = self.classifier(input_ids, attention_mask)
        pred = torch.argmax(logits, dim=1).item()
        levels = ["low", "medium", "high"]
        return levels[pred]

    def _refine_low(self, question: str, sql: str, schema_text: str) -> str:
        prompt = (
            f"Correct minor syntax issues in this SQL query while keeping the logic exactly the same.\n"
            f"Question: {question}\n"
            f"Schema: {schema_text}\n"
            f"SQL: {sql}\n"
            f"Corrected SQL:"
        )
        return self._run_generator(prompt)

    def _refine_medium(self, question: str, sql: str, schema_text: str) -> str:
        prompt = (
            f"Improve the structure of this SQL query so that it follows best practices "
            f"(explicit JOINs, proper GROUP BY, consistent clauses) while accurately answering the question.\n"
            f"Question: {question}\n"
            f"Schema: {schema_text}\n"
            f"Original SQL: {sql}\n"
            f"Refined SQL:"
        )
        return self._run_generator(prompt)

    def _refine_high(self, question: str, sql: str, schema_text: str) -> str:
        prompt = (
            f"You are an expert SQL developer. The query below may contain semantic errors or be non‑executable.\n"
            f"Reason step‑by‑step about the question and the schema, then rewrite the SQL to be correct and efficient.\n"
            f"Question: {question}\n"
            f"Schema: {schema_text}\n"
            f"Current SQL: {sql}\n"
            f"Your analysis and final SQL:"
        )
        return self._run_generator(prompt, max_new_tokens=384)

    def _run_generator(self, prompt: str, max_new_tokens: int = 256) -> str:
        inputs = self.generator_tokenizer(
            prompt, return_tensors="pt", truncation=True, max_length=512
        ).to(self.device)
        with torch.no_grad():
            outputs = self.generator.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                num_beams=1,
                do_sample=False,
            )
        return self.generator_tokenizer.decode(outputs[0], skip_special_tokens=True).strip()

    def _execution_check(self, sql: str) -> bool:
        if self.db_executor:
            try:
                self.db_executor(sql)
                return True
            except Exception:
                return False
        return True  # placeholder: assume executable

    def _quality_score(self, sql: str, question: str) -> float:
        score = 0
        sql_upper = sql.upper()
        required_keywords = ["SELECT", "FROM"]
        if all(kw in sql_upper for kw in required_keywords):
            score += 50
        if "JOIN" in sql_upper or "WHERE" in sql_upper:
            score += 30
        if 10 < len(sql) < 500:
            score += 20
        return min(score, 100)

    def _refine_with_fallback(self, question: str, initial_sql: str,
                              schema_text: str, complexity: str) -> Dict:
        refiner_map = {
            "low": self._refine_low,
            "medium": self._refine_medium,
            "high": self._refine_high,
        }
        refined_sql = refiner_map[complexity](question, initial_sql, schema_text)

        init_exec = self._execution_check(initial_sql)
        ref_exec  = self._execution_check(refined_sql)

        if ref_exec and not init_exec:
            return {"sql": refined_sql, "fallback": "refined_only_exec"}
        elif init_exec and not ref_exec:
            return {"sql": initial_sql, "fallback": "kept_initial"}
        elif init_exec and ref_exec:
            q_init = self._quality_score(initial_sql, question)
            q_ref  = self._quality_score(refined_sql, question)
            winner = refined_sql if q_ref > q_init else initial_sql
            return {"sql": winner, "fallback": "quality_comparison"}
        else:
            if complexity != "high":
                escalated = "medium" if complexity == "low" else "high"
                return self._refine_with_fallback(question, initial_sql, schema_text, escalated)
            else:
                return {"sql": initial_sql, "fallback": "max_escalation_default"}

    # ------------------------- Main predict with logging -------------------------
    def predict(self, question: str, schema: Dict[str, List[str]], verbose: bool = True) -> Dict:
        """Full pipeline with optional verbose logging."""
        if verbose:
            print("\n" + "=" * 60)
            print(f"Question: {question}")
            print(f"Schema: {self._format_schema(schema)}")

        # Stage 1: Schema selection
        filtered_schema = self._select_schema(question, schema)
        schema_text = self._format_schema(filtered_schema)
        if verbose:
            print(f"[Stage 1] Filtered schema: {schema_text}")

        # Stage 2: Skeleton generation
        skeleton = self._generate_skeleton(question, schema_text)
        if verbose:
            print(f"[Stage 2] Skeleton: {skeleton}")

        # Stage 3: Initial SQL
        initial_sql = self._generate_sql(question, skeleton, schema_text)
        if verbose:
            print(f"[Stage 3] Initial SQL: {initial_sql}")

        # Stage 4: Complexity classification
        complexity = self._classify_complexity(question, initial_sql, schema_text)
        if verbose:
            print(f"[Stage 4] Complexity: {complexity}")

        # Stage 5: Refinement with fallback
        refined_result = self._refine_with_fallback(
            question, initial_sql, schema_text, complexity
        )
        if verbose:
            print(f"[Stage 5] Refined SQL: {refined_result['sql']}")
            print(f"[Stage 5] Fallback decision: {refined_result['fallback']}")
            print("=" * 60 + "\n")

        return {
            "sql": refined_result["sql"],
            "selected_schema": filtered_schema,
            "skeleton": skeleton,
            "complexity": complexity,
            "fallback": refined_result["fallback"],
        }
