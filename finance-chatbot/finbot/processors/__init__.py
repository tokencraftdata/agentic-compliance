"""Payment processor orchestration: adapters, routing rules, cascade policy, engine, simulator.

    model       value types (Payment, Attempt, RoutingTrace, Outcome)
    codes       canonical response codes shared by every processor
    adapters    one adapter per processor dialect (adapter pattern, registry)
    catalog     processor capabilities, fees and incidents (configuration)
    routing     rule chain that ranks processors (strategy + chain of responsibility)
    cascade     may a failed attempt go to the next processor? (policy in code)
    engine      PaymentOrchestrator: route -> attempt -> normalise -> cascade -> trace
    simulator   seeded simulated processors and scenarios
    explain     read-side helpers the support bot's tools use
"""
from .cascade import CascadePolicy
from .catalog import Incident, ProcessorDirectory, ProcessorProfile
from .engine import PaymentOrchestrator, ProcessorUnavailable
from .model import Attempt, CascadeDecision, Normalized, Outcome, Payment, RoutePlan, RouteStep, RoutingTrace
from .routing import Router

__all__ = ["Attempt", "CascadeDecision", "CascadePolicy", "Incident", "Normalized", "Outcome", "Payment",
           "PaymentOrchestrator", "ProcessorDirectory", "ProcessorProfile", "ProcessorUnavailable", "RoutePlan",
           "RouteStep", "Router", "RoutingTrace"]
