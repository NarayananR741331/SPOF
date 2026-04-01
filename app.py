#!/usr/bin/env python3
"""
SPOF Analyzer - Single Point of Failure Detection & Analysis
A Python Flask web application that analyzes architecture diagrams and design documents
to identify single points of failure. No AI required - all analysis runs locally.

Supported file types:
- .drawio (XML-based architecture diagrams)
- .vsdx/.vsd (Visio diagrams - limited support)
- .docx (Word documents)
- .doc (Legacy Word - limited support)
- Images (.png, .jpg, .svg, etc.)

Usage:
    pip install flask python-docx lxml
    python app.py
"""

import os
import xml.etree.ElementTree as ET
from collections import defaultdict
from io import BytesIO
from typing import Dict, List, Optional, Set, Tuple

from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max upload
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {
    'drawio', 'vsdx', 'vsd',
    'docx', 'doc',
    'png', 'jpg', 'jpeg', 'gif', 'bmp', 'svg', 'webp'
}


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_file_type(filename: str) -> str:
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext in ('drawio',):
        return 'diagram'
    if ext in ('vsdx', 'vsd'):
        return 'visio'
    if ext in ('docx', 'doc'):
        return 'document'
    if ext in ('png', 'jpg', 'jpeg', 'gif', 'bmp', 'svg', 'webp'):
        return 'image'
    return 'unknown'


# ============================================================
# DrawIO XML Parser
# ============================================================

def parse_drawio_xml(xml_text: str) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Parse a .drawio XML file and extract nodes and edges."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], []

    nodes: Dict[str, str] = {}  # id -> label
    edges: List[Tuple[str, str]] = []  # (source_id, target_id)

    # Find all mxCell elements (they can be nested in various ways)
    for cell in root.iter('mxCell'):
        cell_id = cell.get('id', '')
        value = cell.get('value', '')
        source = cell.get('source')
        target = cell.get('target')
        style = cell.get('style', '')

        if source and target:
            edges.append((source, target))
        elif value and cell_id and 'swimlane' not in style and cell_id not in ('0', '1'):
            # Strip HTML tags from value
            import re
            clean_value = re.sub(r'<[^>]*>', '', value).strip()
            if clean_value:
                nodes[cell_id] = clean_value

    # Resolve edges: replace IDs with node labels
    node_values = set(nodes.values())
    resolved_edges = []
    for src, tgt in edges:
        src_name = nodes.get(src, src)
        tgt_name = nodes.get(tgt, tgt)
        if src_name in node_values and tgt_name in node_values:
            resolved_edges.append((src_name, tgt_name))

    return list(node_values), resolved_edges


# ============================================================
# Graph Analysis - Articulation Points (DFS-based)
# ============================================================

def find_articulation_points(nodes: List[str], edges: List[Tuple[str, str]]) -> List[str]:
    """Find articulation points in an undirected graph using Tarjan's algorithm."""
    if not nodes:
        return []

    adj: Dict[str, Set[str]] = defaultdict(set)
    for src, tgt in edges:
        adj[src].add(tgt)
        adj[tgt].add(src)

    visited: Set[str] = set()
    disc: Dict[str, int] = {}
    low: Dict[str, int] = {}
    parent: Dict[str, Optional[str]] = {}
    ap: Set[str] = set()
    time_counter = [0]

    def dfs(u: str):
        children = 0
        visited.add(u)
        disc[u] = low[u] = time_counter[0]
        time_counter[0] += 1

        for v in adj.get(u, set()):
            if v not in visited:
                children += 1
                parent[v] = u
                dfs(v)
                low[u] = min(low[u], low[v])

                # u is an articulation point if:
                # 1. u is root and has 2+ children
                if parent[u] is None and children > 1:
                    ap.add(u)
                # 2. u is not root and low[v] >= disc[u]
                if parent[u] is not None and low[v] >= disc[u]:
                    ap.add(u)

            elif v != parent.get(u):
                low[u] = min(low[u], disc[v])

    for node in nodes:
        if node not in visited:
            parent[node] = None
            dfs(node)

    return list(ap)


def find_single_connection_nodes(nodes: List[str], edges: List[Tuple[str, str]]) -> List[str]:
    """Find nodes with only one connection (leaf nodes)."""
    connection_count: Dict[str, int] = defaultdict(int)
    for src, tgt in edges:
        connection_count[src] += 1
        connection_count[tgt] += 1

    if len(edges) <= 1:
        return []

    return [n for n in nodes if connection_count.get(n, 0) == 1]


def find_hub_nodes(nodes: List[str], edges: List[Tuple[str, str]], threshold: int = 3) -> List[str]:
    """Find nodes with many connections (potential bottlenecks)."""
    connection_count: Dict[str, int] = defaultdict(int)
    for src, tgt in edges:
        connection_count[src] += 1
        connection_count[tgt] += 1

    return [n for n in nodes if connection_count.get(n, 0) >= threshold]


# ============================================================
# Graph-Based SPOF Analysis
# ============================================================

def analyze_graph_for_spof(nodes: List[str], edges: List[Tuple[str, str]]) -> List[dict]:
    """Analyze graph topology for single points of failure."""
    spofs = []

    # Find articulation points (critical SPOFs)
    articulation_pts = find_articulation_points(nodes, edges)
    for node in articulation_pts:
        spofs.append({
            'component': node,
            'severity': 'critical',
            'description': f'"{node}" is an articulation point in the architecture graph. '
                           f'Removing it would disconnect parts of the system.',
            'recommendation': f'Add redundancy for "{node}". Implement failover mechanisms '
                              f'or duplicate this component.',
            'category': 'Graph Topology'
        })

    # Find hub nodes (high connectivity = potential bottleneck)
    hub_nodes = find_hub_nodes(nodes, edges)
    for node in hub_nodes:
        if node not in articulation_pts:
            spofs.append({
                'component': node,
                'severity': 'high',
                'description': f'"{node}" is a hub node with many connections. '
                               f'It may be a bottleneck or single point of failure under load.',
                'recommendation': f'Consider distributing the load from "{node}" across '
                                  f'multiple instances or adding a load balancer.',
                'category': 'Graph Topology'
            })

    # Find single-connection nodes (leaf nodes)
    single_nodes = find_single_connection_nodes(nodes, edges)
    for node in single_nodes:
        if node not in articulation_pts:
            spofs.append({
                'component': node,
                'severity': 'medium',
                'description': f'"{node}" has only a single connection in the architecture. '
                               f'If its connection fails, this component becomes isolated.',
                'recommendation': f'Add redundant connections for "{node}" or implement '
                                  f'fallback mechanisms.',
                'category': 'Graph Topology'
            })

    return spofs


# ============================================================
# DOCX Parser
# ============================================================

def parse_docx(file_bytes: bytes) -> str:
    """Extract text from a .docx file."""
    try:
        from docx import Document
        doc = Document(BytesIO(file_bytes))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return '\n'.join(paragraphs)
    except Exception as e:
        return f"Error parsing DOCX: {str(e)}"


# ============================================================
# Text-Based SPOF Pattern Analysis
# ============================================================

def analyze_text_for_spof(text: str) -> List[dict]:
    """Analyze text content for SPOF patterns using regex matching."""
    import re
    spofs = []
    lower_text = text.lower()

    patterns = [
        {
            'pattern': r'single\s+(database|db|data\s*store)',
            'component': 'Single Database',
            'severity': 'critical',
            'category': 'Data Layer',
            'description': 'A single database instance without replication or failover creates a critical single point of failure.',
            'recommendation': 'Implement database replication with automatic failover. Consider primary-replica or multi-master setup.'
        },
        {
            'pattern': r'single\s+(server|instance|node|host)',
            'component': 'Single Server Instance',
            'severity': 'critical',
            'category': 'Compute Layer',
            'description': 'A single server instance means any hardware or software failure takes down the entire service.',
            'recommendation': 'Deploy multiple server instances behind a load balancer with health checks and auto-scaling.'
        },
        {
            'pattern': r'no\s+(failover|redundancy|backup|replication|ha|high.availability)',
            'component': 'Missing Failover/Redundancy',
            'severity': 'critical',
            'category': 'Infrastructure',
            'description': 'The architecture explicitly lacks failover or redundancy mechanisms.',
            'recommendation': 'Implement failover mechanisms at every critical layer. Use active-passive or active-active configurations.'
        },
        {
            'pattern': r'single\s+(load\s*balancer|lb|proxy|gateway|api\s*gateway)',
            'component': 'Single Load Balancer/Gateway',
            'severity': 'critical',
            'category': 'Network Layer',
            'description': 'A single load balancer or API gateway is itself a single point of failure.',
            'recommendation': 'Deploy redundant load balancers using DNS failover, floating IPs, or a global load balancing service.'
        },
        {
            'pattern': r'(monolith|monolithic)\s*(application|architecture|system|service)',
            'component': 'Monolithic Architecture',
            'severity': 'high',
            'category': 'Architecture',
            'description': 'A monolithic architecture concentrates all functionality in one deployment unit, making the entire system vulnerable to single failures.',
            'recommendation': 'Consider decomposing into microservices or at least separate critical components with independent scaling and failover.'
        },
        {
            'pattern': r'single\s+(region|datacenter|data\s*center|availability\s*zone|az)',
            'component': 'Single Region/Datacenter',
            'severity': 'high',
            'category': 'Infrastructure',
            'description': 'Running in a single region or datacenter means a regional outage takes down everything.',
            'recommendation': 'Implement multi-region or multi-AZ deployment with cross-region replication and failover.'
        },
        {
            'pattern': r'(shared|common|central)\s*(storage|file\s*system|disk|volume|nfs)',
            'component': 'Shared Storage',
            'severity': 'high',
            'category': 'Data Layer',
            'description': 'A shared storage system creates a dependency that can fail and affect all connected services.',
            'recommendation': 'Use distributed storage solutions (e.g., S3, GCS, Ceph) with built-in replication.'
        },
        {
            'pattern': r'single\s+(queue|message\s*broker|kafka|rabbitmq|sqs)',
            'component': 'Single Message Queue',
            'severity': 'high',
            'category': 'Messaging Layer',
            'description': 'A single message queue/broker instance creates a SPOF in async communication.',
            'recommendation': 'Deploy clustered message brokers with replication (e.g., Kafka cluster, RabbitMQ cluster).'
        },
        {
            'pattern': r'(no|without|lacking|missing)\s*(monitoring|alerting|health.check|observability)',
            'component': 'Missing Monitoring',
            'severity': 'medium',
            'category': 'Operations',
            'description': 'Without monitoring and alerting, failures may go undetected, extending downtime.',
            'recommendation': 'Implement comprehensive monitoring, health checks, and alerting at all infrastructure layers.'
        },
        {
            'pattern': r'(manual|hand|human)\s*(failover|recovery|intervention|restart|deployment)',
            'component': 'Manual Recovery Process',
            'severity': 'medium',
            'category': 'Operations',
            'description': 'Manual recovery processes increase downtime and are error-prone.',
            'recommendation': 'Automate failover and recovery processes. Implement self-healing infrastructure with automated health checks.'
        },
        {
            'pattern': r'single\s*(dns|domain\s*name)',
            'component': 'Single DNS Provider',
            'severity': 'medium',
            'category': 'Network Layer',
            'description': 'Relying on a single DNS provider can lead to complete unavailability if the provider fails.',
            'recommendation': 'Use multiple DNS providers or a DNS service with built-in redundancy (e.g., Route53, Cloudflare).'
        },
        {
            'pattern': r'(no|without|lacking)\s*(auto.?scaling|scaling|elasticity)',
            'component': 'No Auto-Scaling',
            'severity': 'medium',
            'category': 'Compute Layer',
            'description': 'Without auto-scaling, the system cannot handle traffic spikes, leading to potential outages.',
            'recommendation': 'Implement auto-scaling policies based on CPU, memory, or request metrics.'
        },
        {
            'pattern': r'single\s*(cache|redis|memcache|cdn)',
            'component': 'Single Cache Instance',
            'severity': 'medium',
            'category': 'Data Layer',
            'description': 'A single cache instance creates a performance SPOF. Cache failure causes a thundering herd to the database.',
            'recommendation': 'Deploy Redis/Memcached clusters with replication. Implement cache-aside pattern with graceful degradation.'
        },
        {
            'pattern': r'(hard.?coded|static)\s*(config|configuration|credentials|secrets|ip|endpoint)',
            'component': 'Hardcoded Configuration',
            'severity': 'medium',
            'category': 'Configuration',
            'description': 'Hardcoded configurations make it difficult to failover or switch between environments.',
            'recommendation': 'Use dynamic configuration management (e.g., Consul, etcd) and externalize all configuration.'
        },
        {
            'pattern': r'(no|without|lacking|missing)\s*(disaster\s*recovery|dr|backup\s*strategy|backup\s*plan)',
            'component': 'No Disaster Recovery Plan',
            'severity': 'high',
            'category': 'Operations',
            'description': 'Without a disaster recovery plan, major failures can lead to extended or permanent data/service loss.',
            'recommendation': 'Develop and regularly test a disaster recovery plan. Define RPO and RTO targets.'
        },
    ]

    found = set()
    for p in patterns:
        if re.search(p['pattern'], lower_text):
            key = p['component']
            if key not in found:
                found.add(key)
                spofs.append({
                    'component': p['component'],
                    'severity': p['severity'],
                    'description': p['description'],
                    'recommendation': p['recommendation'],
                    'category': p['category']
                })

    # Check for architecture keywords to extract components
    arch_keywords = [
        'load balancer', 'database', 'cache', 'redis', 'memcached',
        'api gateway', 'message queue', 'kafka', 'rabbitmq',
        'web server', 'app server', 'microservice', 'container',
        'kubernetes', 'docker', 'nginx', 'apache', 'cdn',
        'dns', 'firewall', 'proxy', 'storage', 'elastic',
        'lambda', 'serverless', 'vpc', 'subnet'
    ]

    mentioned_components = []
    for kw in arch_keywords:
        if kw in lower_text:
            mentioned_components.append(kw.title())

    return spofs


def extract_components_from_text(text: str) -> List[str]:
    """Extract architecture component names from text."""
    import re
    components = []
    pattern = re.compile(
        r'\b(component|service|server|database|cache|queue|gateway|proxy|'
        r'balancer|cluster|node|instance|container|pod|broker|store|api|'
        r'worker|scheduler|monitor)s?\b',
        re.IGNORECASE
    )
    matches = pattern.findall(text)
    if matches:
        components.extend([m.strip().title() for m in matches])
    return list(set(components))


# ============================================================
# Main Analysis Orchestrator
# ============================================================

def analyze_files(files_data: List[Tuple[str, bytes]]) -> dict:
    """Analyze all uploaded files and return combined results."""
    all_spofs = []
    all_components = []

    for filename, file_bytes in files_data:
        ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''

        if ext == 'drawio':
            xml_text = file_bytes.decode('utf-8', errors='replace')
            nodes, edges = parse_drawio_xml(xml_text)
            all_components.extend(nodes)
            graph_spofs = analyze_graph_for_spof(nodes, edges)
            all_spofs.extend(graph_spofs)

        elif ext == 'docx':
            text = parse_docx(file_bytes)
            all_components.extend(extract_components_from_text(text))
            text_spofs = analyze_text_for_spof(text)
            all_spofs.extend(text_spofs)

        elif ext == 'doc':
            all_spofs.append({
                'component': 'Legacy .doc Format',
                'severity': 'low',
                'description': '.doc files cannot be fully parsed. Please convert to .docx format for complete analysis.',
                'recommendation': 'Save the .doc file as .docx format using Microsoft Word or LibreOffice, then re-upload.',
                'category': 'Input'
            })

        elif ext in ('vsdx', 'vsd'):
            all_spofs.append({
                'component': 'Visio Format',
                'severity': 'low',
                'description': 'Visio files (.vsdx/.vsd) cannot be fully parsed locally. Export as .drawio for graph analysis.',
                'recommendation': 'Export the Visio diagram as a .drawio file (using draw.io import) or as an image, then re-upload.',
                'category': 'Input'
            })

        elif get_file_type(filename) == 'image':
            all_spofs.append({
                'component': 'Image Architecture Diagram',
                'severity': 'low',
                'description': 'Image-based diagrams cannot be automatically parsed for graph structure. Upload the source .drawio file for full SPOF detection.',
                'recommendation': 'If created in draw.io, upload the original .drawio file. Otherwise, describe the architecture in a .docx document.',
                'category': 'Input'
            })

    # Deduplicate SPOFs
    seen = set()
    unique_spofs = []
    for spof in all_spofs:
        key = f"{spof['component']}-{spof['category']}"
        if key not in seen:
            seen.add(key)
            unique_spofs.append(spof)

    # Sort by severity
    severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    unique_spofs.sort(key=lambda s: severity_order.get(s['severity'], 4))

    # Determine overall risk
    overall_risk = 'low'
    if any(s['severity'] == 'critical' for s in unique_spofs):
        overall_risk = 'critical'
    elif any(s['severity'] == 'high' for s in unique_spofs):
        overall_risk = 'high'
    elif any(s['severity'] == 'medium' for s in unique_spofs):
        overall_risk = 'medium'

    # Count by severity
    counts = {
        'critical': sum(1 for s in unique_spofs if s['severity'] == 'critical'),
        'high': sum(1 for s in unique_spofs if s['severity'] == 'high'),
        'medium': sum(1 for s in unique_spofs if s['severity'] == 'medium'),
        'low': sum(1 for s in unique_spofs if s['severity'] == 'low'),
    }

    # Summary
    if not unique_spofs:
        summary = ('No single points of failure were identified. '
                    'Consider providing more detailed architecture documentation.')
    else:
        summary = (f'Found {len(unique_spofs)} potential single point(s) of failure: '
                   f'{counts["critical"]} critical, {counts["high"]} high, '
                   f'{counts["medium"]} medium, {counts["low"]} low severity.')

    unique_components = list(set(all_components))

    return {
        'spofs': unique_spofs,
        'summary': summary,
        'overall_risk': overall_risk,
        'counts': counts,
        'components': unique_components,
        'total': len(unique_spofs),
    }


# ============================================================
# Flask Routes
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/analyze', methods=['POST'])
def analyze():
    if 'files' not in request.files:
        return jsonify({'error': 'No files uploaded'}), 400

    files = request.files.getlist('files')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No files selected'}), 400

    files_data = []
    for f in files:
        if f.filename and allowed_file(f.filename):
            file_bytes = f.read()
            files_data.append((f.filename, file_bytes))

    if not files_data:
        return jsonify({'error': 'No valid files uploaded. Supported: .drawio, .vsdx, .vsd, .docx, .doc, images'}), 400

    result = analyze_files(files_data)
    return jsonify(result)


# ============================================================
# Entry Point
# ============================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  SPOF Analyzer - Single Point of Failure Detection")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 60)
    app.run(debug=True, host='0.0.0.0', port=5000)
