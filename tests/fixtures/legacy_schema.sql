-- Schema of the database BEFORE the public-API-v2 migration: what create_all produced from
-- application/utils/db.py at commit ef1104d (pg_dump --schema-only, psql meta-commands removed).
-- Used by tests/test_migrations.py to prove the migration upgrades a real legacy database.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE TABLE public.api_usage_daily (
    client_id integer NOT NULL,
    date date NOT NULL,
    request_count integer NOT NULL
);
CREATE TABLE public.client_event_types (
    client_id integer NOT NULL,
    event_type character varying NOT NULL,
    label character varying NOT NULL,
    weight double precision NOT NULL,
    created_at timestamp with time zone NOT NULL
);
CREATE TABLE public.clients (
    id integer NOT NULL,
    name character varying NOT NULL,
    secret_key_hash character varying,
    secret_key_rotated_at timestamp with time zone,
    public_key_hash character varying,
    public_key_rotated_at timestamp with time zone,
    plan character varying DEFAULT 'free'::character varying NOT NULL,
    is_active boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    supabase_user_id character varying,
    contact_email character varying,
    last_used_at timestamp with time zone
);
CREATE SEQUENCE public.clients_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.clients_id_seq OWNED BY public.clients.id;
CREATE TABLE public.interactions (
    id integer NOT NULL,
    client_id integer NOT NULL,
    product_type character varying NOT NULL,
    work_id integer NOT NULL,
    user_id integer NOT NULL,
    event_type character varying NOT NULL,
    quantity integer NOT NULL,
    occurred_at timestamp with time zone NOT NULL
);
CREATE SEQUENCE public.interactions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.interactions_id_seq OWNED BY public.interactions.id;
CREATE TABLE public.model_versions (
    id integer NOT NULL,
    client_id integer NOT NULL,
    product_type character varying NOT NULL,
    trained_at timestamp with time zone NOT NULL,
    factors integer NOT NULL,
    regularization double precision NOT NULL,
    iterations integer NOT NULL,
    precision_at_k double precision,
    num_users integer,
    num_items integer,
    num_interactions integer,
    file_path character varying NOT NULL,
    is_active boolean NOT NULL,
    triggered_by character varying(10) NOT NULL
);
CREATE SEQUENCE public.model_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.model_versions_id_seq OWNED BY public.model_versions.id;
CREATE TABLE public.products (
    client_id integer NOT NULL,
    product_type character varying NOT NULL,
    work_id integer NOT NULL,
    title character varying NOT NULL,
    description character varying,
    genre_1 character varying,
    author character varying,
    year integer,
    url character varying,
    price double precision,
    updated_at timestamp with time zone NOT NULL,
    embedding public.vector(384)
);
CREATE TABLE public.users (
    client_id integer NOT NULL,
    product_type character varying NOT NULL,
    user_id integer NOT NULL,
    user_gender character varying,
    user_age integer,
    user_zip integer,
    user_firstname character varying,
    user_lastname character varying
);
ALTER TABLE ONLY public.clients ALTER COLUMN id SET DEFAULT nextval('public.clients_id_seq'::regclass);
ALTER TABLE ONLY public.interactions ALTER COLUMN id SET DEFAULT nextval('public.interactions_id_seq'::regclass);
ALTER TABLE ONLY public.model_versions ALTER COLUMN id SET DEFAULT nextval('public.model_versions_id_seq'::regclass);
ALTER TABLE ONLY public.api_usage_daily
    ADD CONSTRAINT api_usage_daily_pkey PRIMARY KEY (client_id, date);
ALTER TABLE ONLY public.client_event_types
    ADD CONSTRAINT client_event_types_pkey PRIMARY KEY (client_id, event_type);
ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_public_key_hash_key UNIQUE (public_key_hash);
ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_secret_key_hash_key UNIQUE (secret_key_hash);
ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_supabase_user_id_key UNIQUE (supabase_user_id);
ALTER TABLE ONLY public.interactions
    ADD CONSTRAINT interactions_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_pkey PRIMARY KEY (client_id, product_type, work_id);
ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (client_id, product_type, user_id);
CREATE INDEX ix_interactions_lookup ON public.interactions USING btree (client_id, product_type, event_type, user_id, work_id);
CREATE INDEX ix_model_versions_lookup ON public.model_versions USING btree (client_id, product_type, is_active);
ALTER TABLE ONLY public.api_usage_daily
    ADD CONSTRAINT api_usage_daily_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.client_event_types
    ADD CONSTRAINT client_event_types_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.interactions
    ADD CONSTRAINT interactions_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);
